"""iOS / Android Chrome 共用驱动，只执行真实 UI 和只读页面检查。"""
from __future__ import annotations

import base64
import re
import urllib.error
from pathlib import Path
from urllib.parse import urlsplit

from web_ui_iphone_ui import Driver as LegacyDriver, ELEMENT_KEY, BASE


class MobileCompatibilityError(RuntimeError):
    """设备或浏览器状态不明确，停止动作，避免点击错误位置。"""


class MobileSessionUnavailable(MobileCompatibilityError):
    """驱动已断开或命令超时，保留检查点，禁止把未知动作自动重试。"""


def native_tree(tree):
    """将 UiAutomator2 bounds/text 转为公共原生节点属性，保留原属性。"""
    for node in tree.iter():
        attrs = node.attrib
        match = re.fullmatch(r'\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]', attrs.get('bounds', ''))
        if match:
            left, top, right, bottom = map(int, match.groups())
            attrs.update(x=str(left), y=str(top), width=str(right-left), height=str(bottom-top))
            attrs['visible'] = str(attrs.get('displayed', 'true') != 'false' and right > left and bottom > top).lower()
            attrs.setdefault('name', attrs.get('content-desc') or attrs.get('text') or attrs.get('resource-id', ''))
            attrs.setdefault('label', attrs.get('text', ''))
            attrs.setdefault('value', attrs.get('text', ''))
    return tree


def map_web_point(rect, viewport, bounds, x_fraction=.5, y_fraction=.5):
    """CSS 可视视口映射到原生内容区域；比例不符时拒绝猜坐标。"""
    width, height = viewport['width'], viewport['height']
    if width <= 0 or height <= 0 or bounds['width'] <= 0 or bounds['height'] <= 0:
        raise MobileCompatibilityError('网页或原生视口尺寸无效')
    sx, sy = bounds['width']/width, bounds['height']/height
    if abs(sx-sy)/max(sx, sy) > .08:
        raise MobileCompatibilityError('原生 WebView 与网页视口比例不符，停止触屏动作')
    x = rect['x'] + rect['width']*x_fraction - viewport.get('offsetLeft', 0)
    y = rect['y'] + rect['height']*y_fraction - viewport.get('offsetTop', 0)
    if not (0 <= x < width and 0 <= y < height):
        raise MobileCompatibilityError('目标不在网页可视区域，停止触屏动作')
    return round(bounds['x']+x*sx), round(bounds['y']+y*sy)


class MobileDriver(LegacyDriver):
    def __init__(self, session_id, *, endpoint=BASE, output_dir=None, capabilities=None):
        """绑定已有移动会话及证据目录；capabilities可供离线校验注入，不创建新会话。"""
        super().__init__(session_id)
        self.url = endpoint.rstrip('/') + '/session/' + session_id
        self.output_dir = Path(output_dir) if output_dir else Path(__file__).parent/'web_ui_artifacts'
        self._capabilities = capabilities

    def request(self, path, data=None, *, timeout=None):
        """只请求一次；驱动断连快速上报，点击等未知结果不自动重发。"""
        if getattr(self,'_session_blocker',None):
            raise MobileSessionUnavailable(self._session_blocker)
        limit=timeout if timeout is not None else (10 if path=='/source' else 45)
        try:return super().request(path,data,timeout=limit)
        except (TimeoutError,ConnectionError,urllib.error.URLError) as error:
            self._session_blocker='手机自动化命令超时或连接中断；保存现场后停止本阶段，不重发动作。'
            raise MobileSessionUnavailable(self._session_blocker) from error
        except RuntimeError as error:
            if any(text in str(error).lower() for text in ('instrumentation process is not running','invalid session id','socket hang up','connection refused')):
                self._session_blocker='手机自动化会话已失效；本阶段停止，恢复连接后从检查点继续。'
                raise MobileSessionUnavailable(self._session_blocker) from error
            raise

    def health(self):
        """短时读取当前页面以检查连接，丢弃正文；不刷新网页或修改权限。"""
        self._session_blocker=None
        self.request('/source',timeout=8)
        return {'status':'ready'}

    @property
    def capabilities(self):
        """首次读取并缓存Appium真实会话能力，后续不重复查询。"""
        if self._capabilities is None:
            # Android网页上下文会把GET session代理给Chromedriver，该路由非W3C标准。
            self.request('/context', {'name':'NATIVE_APP'})
            data = self.request('')
            self._capabilities = data.get('capabilities', data)
        return self._capabilities

    @property
    def platform(self):
        """从真实会话能力识别android或ios；未知平台阻止执行。"""
        platform = str(self.capabilities.get('platformName', '')).lower()
        if platform not in ('android', 'ios'):
            raise MobileCompatibilityError('会话缺少有效的 Android / iOS 平台信息')
        return platform

    def device_info(self):
        """返回真实平台、驱动、型号、系统版本及唯一标识，用于批次隔离和报告。"""
        caps = self.capabilities
        get = lambda key: caps.get('appium:'+key, caps.get(key, ''))
        return {'platform': self.platform, 'automation': get('automationName'),
                'name': get('deviceName') or self.platform, 'version': get('platformVersion'),
                'udid': get('udid') or get('deviceUDID'), 'browser': caps.get('browserName') or 'Chrome'}

    def tree(self):
        """读取原生XML并规范Android节点属性，使同一套定位逻辑兼容两种系统。"""
        return native_tree(super().tree())

    def names(self):
        """读取可见原生控件名称集合，供平台提示和页面状态判断使用。"""
        names = set()
        for node in self.tree().iter():
            if node.attrib.get('visible') == 'true':
                names.update(node.attrib.get(key, '') for key in ('name', 'text', 'content-desc', 'resource-id'))
        return names - {''}

    def screenshot(self, name):
        """以name为证据名保存当前屏幕截图，返回图片绝对路径。"""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir/(name+'.png')
        path.write_bytes(base64.b64decode(str(self.request('/screenshot'))))
        return str(path)

    def find(self, name, index=0):
        """按平台用可访问名称或Android文本、描述、资源ID定位，返回W3C元素ID。"""
        if self.platform == 'ios':
            return super().find(name, index)
        # XPath 字面量不能借用 JSON 双引号转义。
        if '"' not in name:
            literal = '"'+name+'"'
        elif "'" not in name:
            literal = "'"+name+"'"
        else:
            parts = name.split("'")
            literal = 'concat('+', "\'", '.join("'"+part+"'" for part in parts)+')'
        nodes = self.request('/elements', {'using': 'xpath', 'value':
            f'//*[@text={literal} or @content-desc={literal} or @resource-id={literal}]'})
        if len(nodes) <= index:
            raise AssertionError('找不到 Android 原生控件：'+name)
        return nodes[index][ELEMENT_KEY]

    def tap(self, x, y):
        """按真实屏幕尺寸校验坐标后执行W3C触屏动作，兼容Android物理像素和iOS逻辑像素。"""
        self.request('/context', {'name': 'NATIVE_APP'})
        screen = self.request('/window/rect')
        if not (0 <= x < screen['width'] and 0 <= y < screen['height']):
            raise MobileCompatibilityError('原生点击坐标超出屏幕')
        self.request('/actions', {'actions': [{'type': 'pointer', 'id': 'finger1',
            'parameters': {'pointerType': 'touch'}, 'actions': [
                {'type': 'pointerMove', 'duration': 0, 'x': int(x), 'y': int(y)},
                {'type': 'pointerDown', 'button': 0}, {'type': 'pause', 'duration': 80},
                {'type': 'pointerUp', 'button': 0}]}]})

    def swipe_up(self):
        """依据实际屏幕宽高按比例滑动，不固定手机分辨率。"""
        self.request('/context', {'name': 'NATIVE_APP'})
        screen = self.request('/window/rect')
        x, top, bottom = round(screen['width']*.5), round(screen['height']*.3), round(screen['height']*.77)
        self.request('/actions', {'actions': [{'type': 'pointer', 'id': 'finger1',
            'parameters': {'pointerType': 'touch'}, 'actions': [
                {'type': 'pointerMove', 'duration': 0, 'x': x, 'y': bottom},
                {'type': 'pointerDown', 'button': 0},
                {'type': 'pointerMove', 'duration': 500, 'x': x, 'y': top},
                {'type': 'pointerUp', 'button': 0}]}]})

    def focus_page(self, page):
        """按页面记录切换上下文，Android再选择对应Chrome窗口。"""
        self.request('/context', {'name': page['context']})
        if page.get('window'):
            self.request('/window', {'handle': page['window']})

    def activate_page(self, page):
        """选择网页并将Android对应Chrome标签置于真实前台，iOS沿用既有选择。"""
        self.focus_page(page)
        if self.platform=='android' and page.get('window'):
            self.request('/goog/cdp/execute',{'cmd':'Target.activateTarget',
                         'params':{'targetId':page['window'].removeprefix('CDwindow-')}})

    def select_page(self, prefix, *, visible=True):
        """iOS 按 WebView 找页面，Android 另遍历 Chrome 窗口。"""
        self.request('/context', {'name': 'NATIVE_APP'})
        if self.platform == 'ios':
            contexts = self.request('/execute/sync', {'script': 'mobile: getContexts', 'args': []})
            pages = [{'context': c['id']} for c in reversed(contexts)
                     if c.get('bundleId') == 'com.google.chrome.ios' and c.get('url', '').startswith(prefix)]
        else:
            package = self.request('/appium/device/current_package')
            expected = self.capabilities.get('appium:appPackage', 'com.android.chrome')
            if package != expected:
                return None
            pages = []
            for context in self.request('/contexts'):
                if context == 'NATIVE_APP':
                    continue
                self.request('/context', {'name': context})
                pages.extend({'context': context, 'window': window} for window in self.request('/window/handles'))
        for page in pages:
            try:
                self.focus_page(page)
                # 先核对网址，不向新标签页或未知第三方执行 JS。
                url = self.request('/url')
                if not url.startswith(prefix):
                    continue
                state = self.request('/execute/sync', {'script': 'return document.visibilityState', 'args': []})
                if not visible or state == 'visible':
                    return page
            except MobileSessionUnavailable:
                raise
            except RuntimeError:
                continue
        self.request('/context', {'name': 'NATIVE_APP'})
        return None

    def web_bounds(self, viewport):
        """按平台读取当前Chrome内容区域原生边界，缺少可靠边界时停止坐标映射。"""
        self.request('/context', {'name': 'NATIVE_APP'})
        tree = self.tree()
        if self.platform == 'ios':
            toolbar = next((n for n in tree.iter() if n.attrib.get('name') == 'kToolbarNewTabButtonIdentifier'
                            and n.attrib.get('visible') == 'true'), None)
            if toolbar is None:
                raise MobileCompatibilityError('Chrome 工具栏不可见，不能映射触屏坐标')
            return {'x': 0, 'y': float(toolbar.attrib['y'])-viewport['height'],
                    'width': viewport['width'], 'height': viewport['height']}
        candidates = [n.attrib for n in tree.iter() if n.attrib.get('class') == 'android.webkit.WebView'
                      and n.attrib.get('visible') == 'true' and float(n.attrib.get('width', 0)) > 0]
        if not candidates:
            raise MobileCompatibilityError('未找到 Android Chrome 可见 WebView 边界')
        attrs = max(candidates, key=lambda a: float(a['width'])*float(a['height']))
        return {key: float(attrs[key]) for key in ('x', 'y', 'width', 'height')}

    def tap_web_rect(self, rect, *, x_fraction=.5, y_fraction=.5, restore=True):
        """将DOM矩形按可视视口换算为原生触屏坐标；可选择不恢复将关闭的上下文。"""
        context = self.request('/context')
        viewport = self.request('/execute/sync', {'script': '''let v=visualViewport;
            return {width:v?.width??innerWidth,height:v?.height??innerHeight,
            offsetLeft:v?.offsetLeft??0,offsetTop:v?.offsetTop??0}''', 'args': []})
        point = map_web_point(rect, viewport, self.web_bounds(viewport), x_fraction, y_fraction)
        self.tap(*point)
        if restore:
            self.request('/context', {'name': context})

    def fill_web(self, element, text):
        """通过Chromedriver清空并输入指定元素，触发真实输入流程而非JS赋值。"""
        if self.platform=='android':
            current=self.request('/execute/sync',{'script':'let e=arguments[0];return e.isContentEditable?e.innerText:e.value',
                                                 'args':[{ELEMENT_KEY:element}]}) or ''
            # Android的受控表单会在clear后恢复旧值，使用键盘退格触发真实input事件。
            self.request(f'/element/{element}/value',{'text':'\ue010'+'\ue003'*(len(current)+1)+text})
            return
        self.request(f'/element/{element}/clear', {})
        self.request(f'/element/{element}/value', {'text': text})

    def permission_button(self, allow):
        """只定位 Android 系统权限层或 Chrome 站点权限层中的按钮。"""
        self.request('/context', {'name': 'NATIVE_APP'})
        nodes = [n.attrib for n in self.tree().iter() if n.attrib.get('visible') == 'true']
        ids = ('permission_allow_foreground_only_button', 'permission_allow_one_time_button',
               'permission_allow_button') if allow else ('permission_deny_button', 'permission_deny_and_dont_ask_again_button')
        for suffix in ids:
            for node in nodes:
                rid = node.get('resource-id', '')
                if rid.endswith('/'+suffix) and any(token in rid for token in ('permissioncontroller', 'permissionmanager', 'com.android.chrome')):
                    return node
        body = ' '.join(n.get('text', '')+' '+n.get('content-desc', '') for n in nodes)
        host = urlsplit(self.target_url).hostname or ''
        site_request = host in body and re.search(r'camera|microphone|摄像头|相机|麦克风', body, re.I)
        labels = ('允许', 'Allow', 'Allow this time', 'Allow while visiting the site',
                  '仅此次允许', '仅此一次', '在访问期间允许') if allow else (
                  "Don't allow", 'Never allow', '不允许', '一律不允许', '拒绝', 'Block', '阻止')
        for node in nodes:
            if site_request and node.get('package') == 'com.android.chrome' and node.get('text') in labels:
                return node
            if (allow and site_request and node.get('package')=='com.android.chrome'
                    and node.get('resource-id','').startswith('com.android.chrome:')
                    and node.get('text') in ('继续','Continue')
                    and re.search(r'允许使用(?:摄像头|麦克风)|Chrome.*(?:授予|permission)',body,re.I)):
                return node
        return None

    def tap_node(self, attrs):
        """点击最新原生节点边界中心，复用屏幕范围校验和真实触屏动作。"""
        self.tap(round(float(attrs['x'])+float(attrs['width'])/2),
                 round(float(attrs['y'])+float(attrs['height'])/2))

    def assert_no_system_permission_dialog(self):
        """Android权限或文件选择器覆盖网页时阻止业务判定，检查后恢复原上下文。"""
        if self.platform != 'android':
            return
        previous = self.request('/context')
        self.request('/context', {'name':'NATIVE_APP'})
        try:
            nodes=[n.attrib for n in self.tree().iter() if n.attrib.get('visible')=='true']
            blocked=any(any(token in n.get('package','') for token in
                            ('permissioncontroller','permissionmanager','photopicker','documentsui')) for n in nodes)
            body=' '.join(n.get('text','') for n in nodes)
            chrome_prompt=any(n.get('resource-id','').startswith('com.android.chrome:') and n.get('text') in ('继续','Continue') for n in nodes)
            blocked=blocked or bool(chrome_prompt and re.search(r'允许使用(?:摄像头|麦克风)|Chrome.*(?:授予|permission)',body,re.I))
            if blocked:
                raise MobileCompatibilityError('Android系统权限或文件选择器遮挡网页，必须先处理后再核对业务结果')
        finally:
            self.request('/context', {'name':previous})
