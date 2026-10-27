"""为普通和单文件Allure报告应用同一份可替换Logo。"""
import base64
import re
from pathlib import Path

from web_ui_environment import configuration


def apply_branding(report_dir, root=None):
    """内嵌用户提供的Logo作为侧栏图标与favicon，单文件报告无需外部图片。"""
    root = Path(root or Path(__file__).parent)
    logo = root / configuration().get('report_branding', {}).get('logo', 'assets/web_ui_logo.png')
    if not logo.is_file():
        raise FileNotFoundError('报告Logo不存在：' + str(logo))
    uri = 'data:image/png;base64,' + base64.b64encode(logo.read_bytes()).decode('ascii')
    index = Path(report_dir) / 'index.html'
    text = index.read_text(encoding='utf-8')
    text = re.sub(r'<link\b[^>]*rel=["\']icon["\'][^>]*>', '', text)
    text = re.sub(r'<style id="web-ui-branding">.*?</style>\n?', '', text, flags=re.S)
    branding = ('<link rel="icon" href="' + uri + '">'
                '<style id="web-ui-branding">.side-nav__brand{background-image:url("' + uri + '")!important;'
                'background-size:40px 40px!important;background-position:0 center!important;'
                'background-repeat:no-repeat!important;min-height:40px}</style>')
    index.write_text(text.replace('</head>', branding + '\n</head>'), encoding='utf-8')
