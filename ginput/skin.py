"""Render a native Fcitx 4 theme. Optional artwork stays local to the machine."""
import configparser
from datetime import datetime
import math
from pathlib import Path
import shutil

from .core import InputError, atomic_write, xdg

SKIN_NAME = 'ginput-custom'


def build(output: Path, artwork: Path | None = None,
          crop: tuple[int, int, int, int] | None = None, height: int = 140) -> None:
    try:
        import cairo
    except ImportError as exc:
        raise InputError('皮肤渲染需要 python3-cairo。') from exc
    if output.exists() and any(output.iterdir()):
        raise InputError('皮肤输出目录必须为空。')
    if not 48 <= height <= 256:
        raise InputError('装饰图高度须为 48–256 像素。')
    default = Path('/usr/share/fcitx/skin/default')
    if not (default / 'fcitx_skin.conf').exists():
        raise InputError('找不到 Fcitx 4 默认皮肤。')
    # Rendering source coordinates is analogous to placing a sprite in a UI.
    # No artwork file or personal filename is included in the project itself.
    source = cairo.ImageSurface.create_from_png(str(artwork)) if artwork else None
    if source:
        x, y, w, h = crop or (0, 0, source.get_width(), source.get_height())
        if min(x, y) < 0 or min(w, h) <= 0 or x + w > source.get_width() or y + h > source.get_height():
            raise InputError('装饰图裁切区域超出图片范围。')
    output.mkdir(parents=True, exist_ok=True)
    for item in default.glob('*.png'):
        shutil.copyfile(item, output / item.name)

    def panel(name):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 64, 64)
        ctx = cairo.Context(surface)
        # The center/edge regions are uniform so nine-slice resizing is stable.
        radius, a, b = 10, 2.5, 61.5
        for cx, cy, start in ((b-radius, a+radius, -math.pi/2),
                              (b-radius, b-radius, 0),
                              (a+radius, b-radius, math.pi/2),
                              (a+radius, a+radius, math.pi)):
            ctx.arc(cx, cy, radius, start, start+math.pi/2)
        ctx.close_path()
        ctx.set_source_rgba(1, .97, .995, .98)
        ctx.fill_preserve()
        ctx.set_source_rgba(.72, .48, .78, .95)
        ctx.set_line_width(1.5)
        ctx.stroke()
        surface.write_to_png(str(output / name))

    panel('input.png')
    panel('menu.png')
    for name, points in [('prev.png', [(7,2),(2,6),(7,10)]),
                         ('next.png', [(2,2),(7,6),(2,10)])]:
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 10, 12)
        ctx = cairo.Context(surface)
        for i, point in enumerate(points):
            (ctx.move_to if i == 0 else ctx.line_to)(*point)
        ctx.close_path()
        ctx.set_source_rgb(.57,.24,.64)
        ctx.fill()
        surface.write_to_png(str(output/name))
    if source:
        scale = height / h
        width = math.ceil(w * scale)
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        ctx = cairo.Context(surface)
        ctx.scale(scale, scale)
        ctx.set_source_surface(source, -x, -y)
        ctx.get_source().set_filter(cairo.FILTER_BEST)
        ctx.paint()
        surface.write_to_png(str(output/'decoration.png'))

    config = configparser.ConfigParser(interpolation=None)
    config.optionxform = str
    config.read(default/'fcitx_skin.conf')
    updates = {
        'SkinInfo': {'Name': 'Guitar Input', 'Version': '0.3', 'Author': 'Guitar Input contributors', 'Desc': 'Compact native candidate panel'},
        'SkinFont': {'FontSize':'12', 'MenuFontSize':'11', 'RespectDPI':'True',
                     'TipColor':'128 77 143', 'InputColor':'65 35 91', 'IndexColor':'157 112 165',
                     'FirstCandColor':'163 37 133', 'UserPhraseColor':'83 52 112',
                     'CodeColor':'128 93 145', 'OtherColor':'65 35 91',
                     'ActiveMenuColor':'255 255 255', 'InactiveMenuColor':'65 35 91'},
        'SkinInputBar': {'BackImg':'input.png', 'MarginLeft':'14','MarginRight':'28',
                         'MarginTop':'12','MarginBottom':'12','CursorColor':'163 37 133',
                         'InputPos':'0','OutputPos':'6', 'FillVertical':'Resize','FillHorizontal':'Resize',
                         'Overlay':'decoration.png' if source else '', 'OverlayDock':'BottomRight',
                         'OverlayOffsetX':'-6', 'OverlayOffsetY':str(-height),
                         'BackArrow':'prev.png','ForwardArrow':'next.png',
                         'BackArrowX':'24','BackArrowY':'10','ForwardArrowX':'10','ForwardArrowY':'10'},
        'SkinMenu': {'BackImg':'menu.png','MarginTop':'12','MarginBottom':'12','MarginLeft':'12','MarginRight':'12',
                     'ActiveColor':'163 103 177','LineColor':'231 214 235'},
    }
    for section, fields in updates.items():
        if not config.has_section(section): config.add_section(section)
        for key, value in fields.items(): config.set(section,key,value)
    with (output/'fcitx_skin.conf').open('w') as stream: config.write(stream,space_around_delimiters=False)


def activate(source: Path) -> Path:
    if not (source/'fcitx_skin.conf').is_file() or not (source/'input.png').is_file():
        raise InputError('目录不是已生成的 Fcitx 皮肤。')
    fcitx = xdg('XDG_CONFIG_HOME', '.config')/'fcitx'
    target = fcitx/'skin'/SKIN_NAME
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup = xdg('XDG_STATE_HOME','.local/state')/'ginput/skin-backups'/stamp
    config_path = fcitx/'conf/fcitx-classic-ui.config'
    backup.mkdir(parents=True)
    if config_path.exists(): shutil.copy2(config_path,backup/config_path.name)
    if target.exists(): shutil.copytree(target,backup/'skin')
    target.mkdir(parents=True,exist_ok=True)
    for item in source.iterdir():
        if item.is_file() and item.suffix.lower() in ('.png','.conf'):
            atomic_write(target/item.name,item.read_bytes())
    config = configparser.ConfigParser(interpolation=None)
    config.optionxform = str
    if config_path.exists(): config.read(config_path)
    if not config.has_section('ClassicUI'): config.add_section('ClassicUI')
    for key,value in {'SkinType':SKIN_NAME,'Font':'Noto Sans CJK SC','VerticalList':'False'}.items():
        config.set('ClassicUI',key,value)
    import io
    text=io.StringIO()
    config.write(text,space_around_delimiters=False)
    atomic_write(config_path,text.getvalue().encode())
    return backup
