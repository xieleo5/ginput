#!/usr/bin/python3
"""Local GTK candidate renderer. Receives transient state over an inherited socket."""
import argparse
import configparser
import json
import math
import os
from pathlib import Path
import socket

import cairo
import gi
gi.require_foreign('cairo')
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo


class CandidateWindow(Gtk.Window):
    def __init__(self, channel):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.channel = channel
        self.state = {}
        self.cells = []
        self.arrows = []
        self.theme_stamp = None
        self.art = self.accent = None
        self.opacity = .22
        self.set_title('Guitar Input Candidates')
        self.set_accept_focus(False)
        self.set_focus_on_map(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_type_hint(Gdk.WindowTypeHint.COMBO)
        self.set_app_paintable(True)
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual: self.set_visual(visual)
        self.canvas = Gtk.DrawingArea()
        self.canvas.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.POINTER_MOTION_MASK)
        self.canvas.set_has_tooltip(True)
        self.canvas.connect('draw', self.draw)
        self.canvas.connect('button-press-event', self.click)
        self.canvas.connect('scroll-event', self.scroll)
        self.canvas.connect('query-tooltip', self.tooltip)
        self.add(self.canvas)
        self.canvas.show()
        self.font = Pango.FontDescription('Noto Sans CJK SC 12')
        self.small_font = Pango.FontDescription('Noto Sans CJK SC 9')
        self.watch = GLib.io_add_watch(channel.fileno(), GLib.PRIORITY_DEFAULT, GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, self.receive)
        self.connect('destroy', self.cleanup)
        channel.send(b'ready')

    def cleanup(self, _widget):
        if self.watch and GLib.MainContext.default().find_source_by_id(self.watch):
            GLib.source_remove(self.watch)
        self.watch = 0

    def load_theme(self):
        root = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'fcitx/skin/ginput-custom'
        path = root / 'fcitx_skin.conf'
        stamp = path.stat().st_mtime_ns if path.exists() else 0
        if stamp == self.theme_stamp: return
        self.theme_stamp = stamp
        config = configparser.ConfigParser(interpolation=None)
        config.read(path)
        try: self.opacity = min(1., max(0., config.getfloat('GuitarPanel', 'Opacity', fallback=.22)))
        except ValueError: self.opacity = .22
        for attr, name in (('art', 'decoration.png'), ('accent', 'accent.png')):
            try: setattr(self, attr, cairo.ImageSurface.create_from_png(str(root / name)))
            except (cairo.Error, OSError): setattr(self, attr, None)

    def text_layout(self, text, width=None, small=False):
        layout = self.canvas.create_pango_layout(text)
        layout.set_font_description(self.small_font if small else self.font)
        layout.set_single_paragraph_mode(True)
        if width is not None:
            layout.set_width(max(1, int(width)) * Pango.SCALE)
            layout.set_ellipsize(Pango.EllipsizeMode.END)
        return layout

    def receive(self, _fd, condition):
        if condition & (GLib.IO_HUP | GLib.IO_ERR):
            self.hide(); Gtk.main_quit(); return False
        try:
            packet = self.channel.recv(65536)
            if not packet: Gtk.main_quit(); return False
            state = json.loads(packet)
        except (OSError, ValueError):
            self.hide(); Gtk.main_quit(); return False
        if state.get('hide'):
            self.state = {}; self.hide(); return True
        if not isinstance(state.get('words'), list) or len(state['words']) > 20:
            self.hide(); return True
        self.state = state
        self.load_theme()
        self.arrange()
        self.show()
        self.canvas.queue_draw()
        return True

    def arrange(self):
        scale = self.get_scale_factor()
        display = self.get_display()
        anchor_x, anchor_y = int(self.state['x'] / scale), int(self.state['y'] / scale)
        monitor = display.get_monitor_at_point(anchor_x, anchor_y)
        work = monitor.get_workarea()
        art_w = self.art.get_width() / scale if self.art else 0
        art_h = self.art.get_height() / scale if self.art else 0
        accent_w = self.accent.get_width() / scale if self.accent else 0
        accent_h = self.accent.get_height() / scale if self.accent else 0
        self.left = max(0, accent_w - 9)
        pad, top, row_h = 12, 31, 29
        words = self.state['words']
        expanded = self.state['expanded']
        widths = [self.text_layout(f'{i % 5 + 1}. {word}').get_pixel_size()[0] + 16 for i, word in enumerate(words)]
        self.cells = []
        available = max(150, work.width - self.left - art_w - 28)
        if expanded:
            col_widths = [max([88] + [widths[i] for i in range(c, len(words), 5)]) for c in range(5)]
            # Keep all five columns on-screen; full text remains in the tooltip.
            col_widths = [min(w, 155) for w in col_widths]
            factor = min(1., (available - 2 * pad) / sum(col_widths))
            col_widths = [w * factor for w in col_widths]
            body_w = sum(col_widths) + pad * 2
            rows = max(1, math.ceil(len(words) / 5))
            body_h = top + rows * row_h + 26
            for i, word in enumerate(words):
                col, row = i % 5, i // 5
                self.cells.append([self.left + pad + sum(col_widths[:col]), top + row * row_h, col_widths[col], row_h, i, word])
        else:
            col_widths = widths
            desired = max(sum(col_widths) + 2 * pad, self.text_layout(self.state['preedit']).get_pixel_size()[0] + 55, 130)
            body_w = min(desired, available)
            factor = min(1., (body_w - 2 * pad) / max(1, sum(col_widths)))
            col_widths = [w * factor for w in col_widths]
            body_h = top + row_h + 7
            for i, word in enumerate(words):
                self.cells.append([self.left + pad + sum(col_widths[:i]), top, col_widths[i], row_h, i, word])
        self.total_h = math.ceil(max(body_h, art_h, accent_h))
        self.total_w = math.ceil(self.left + body_w + max(0, art_w - 5))
        self.body = (self.left, self.total_h - body_h, body_w, body_h)
        for cell in self.cells: cell[1] += self.body[1]
        self.arrows = [(self.left + body_w - 32, self.body[1] + 5, 14, 22, -1),
                       (self.left + body_w - 17, self.body[1] + 5, 14, 22, 1)]
        self.canvas.set_size_request(self.total_w, self.total_h)
        self.resize(self.total_w, self.total_h)
        # Anchor the text beside the insertion point; decorations may extend left/up.
        x = max(work.x, min(anchor_x - self.left, work.x + work.width - self.total_w))
        y = anchor_y + 3
        if y + self.total_h > work.y + work.height:
            y = anchor_y - int(self.state.get('cursor_height', 0) / scale) - self.total_h - 3
        y = max(work.y, min(y, work.y + work.height - self.total_h))
        self.move(int(x), int(y))

    @staticmethod
    def rounded(ctx, x, y, width, height, radius=7):
        radius = min(radius, width / 2, height / 2)
        ctx.new_sub_path()
        for cx, cy, start in ((x+width-radius,y+radius,-math.pi/2), (x+width-radius,y+height-radius,0),
                              (x+radius,y+height-radius,math.pi/2), (x+radius,y+radius,math.pi)):
            ctx.arc(cx, cy, radius, start, start+math.pi/2)
        ctx.close_path()

    def label(self, ctx, text, x, y, width=None, color=(.22,.05,.36), small=False):
        layout = self.text_layout(text, width, small)
        ctx.set_source_rgb(*color); ctx.move_to(x, y)
        PangoCairo.show_layout(ctx, layout)

    def image(self, ctx, surface, x, y):
        if not surface: return
        ctx.save()
        ctx.translate(x, y)
        scale = self.get_scale_factor()
        ctx.scale(1 / scale, 1 / scale)
        ctx.set_source_surface(surface, 0, 0); ctx.paint(); ctx.restore()

    def draw(self, _widget, ctx):
        ctx.set_operator(cairo.OPERATOR_SOURCE); ctx.set_source_rgba(0,0,0,0); ctx.paint()
        ctx.set_operator(cairo.OPERATOR_OVER)
        if not self.state: return False
        x,y,w,h = self.body
        self.rounded(ctx, x+.5, y+.5, w-1, h-1)
        ctx.set_source_rgba(1, .98, 1, self.opacity); ctx.fill_preserve()
        ctx.set_source_rgba(.66,.32,.78,.20); ctx.set_line_width(.7); ctx.stroke()
        scale = self.get_scale_factor()
        if self.accent: self.image(ctx,self.accent,0,self.total_h-self.accent.get_height()/scale)
        if self.art: self.image(ctx,self.art,x+w-5,self.total_h-self.art.get_height()/scale)
        self.label(ctx,self.state['preedit'],x+12,y+5,w-54,color=(.56,.02,.68))
        selected = self.state.get('selected',0)
        for cx,cy,cw,ch,index,word in self.cells:
            if index == selected:
                self.rounded(ctx,cx-3,cy+1,cw-1,ch-2,5)
                ctx.set_source_rgba(.72,.18,.77,.12); ctx.fill()
            color = (.63,.02,.72) if index == selected else (.22,.05,.36)
            self.label(ctx,f'{index % 5 + 1}. {word}',cx,cy+1,cw-8,color=color)
        for ax,ay,aw,ah,direction in self.arrows:
            enabled = self.state['previous' if direction < 0 else 'next']
            ctx.set_source_rgba(.56,.18,.67,.8 if enabled else .2)
            center= ax+aw/2
            ctx.move_to(center+direction*2,ay+ah/2)
            ctx.line_to(center-direction*2,ay+ah/2-3)
            ctx.line_to(center-direction*2,ay+ah/2+3)
            ctx.close_path(); ctx.fill()
        if self.state['expanded']:
            self.label(ctx,'1–5 选当前行   ·   PgUp / PgDn 翻页',x+12,y+h-22,w-24,color=(.48,.26,.57),small=True)
        return False

    def command(self, name, value):
        if not self.state: return
        try: self.channel.send(f"{name} {self.state['serial']} {value}".encode())
        except OSError: Gtk.main_quit()

    @staticmethod
    def contains(rect, x, y):
        return rect[0] <= x < rect[0]+rect[2] and rect[1] <= y < rect[1]+rect[3]

    def click(self, _widget, event):
        if event.button != 1: return False
        for cell in self.cells:
            if self.contains(cell,event.x,event.y): self.command('select',cell[4]); return True
        for arrow in self.arrows:
            if self.contains(arrow,event.x,event.y): self.command('page',arrow[4]); return True
        return True

    def scroll(self, _widget, event):
        if event.direction == Gdk.ScrollDirection.DOWN: self.command('page',1)
        elif event.direction == Gdk.ScrollDirection.UP: self.command('page',-1)
        return True

    def tooltip(self, _widget, x, y, keyboard, tooltip):
        if keyboard: return False
        for cell in self.cells:
            if self.contains(cell,x,y): tooltip.set_text(cell[5]); return True
        return False


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--fd',type=int,required=True)
    args=parser.parse_args()
    channel=socket.socket(fileno=args.fd)
    window=CandidateWindow(channel)
    Gtk.main()
    channel.close()
    window.destroy()


if __name__ == '__main__': main()
