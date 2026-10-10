"""Draws an on-screen notification as one picture (Pillow): rounded corners, a translucent or blurred (glass)
background, a gradient, a shadow, an accent stripe or a border, an uploaded picture, an icon per level and
wrapped text.

The X display of a node has no compositing window manager, so windows cannot be translucent, rounded or faded.
The overlay therefore takes a picture of the part of the screen the notification is going to cover and draws the
notification into that picture: the window then shows something that looks translucent, rounded and with a shadow.
Fading works the same way (pictures blended in steps). Used by player/overlay.py when Pillow is installed; without
it the overlay keeps its plain Tk drawing.
"""
import functools
import io
import os

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

FONT_DIR = os.getenv('CARACAL_FONT_DIR', '/usr/share/fonts/truetype/dejavu')
FONT_FILES = {'DejaVu Sans': ('DejaVuSans.ttf', 'DejaVuSans-Bold.ttf'),
              'DejaVu Serif': ('DejaVuSerif.ttf', 'DejaVuSerif-Bold.ttf'),
              'DejaVu Sans Mono': ('DejaVuSansMono.ttf', 'DejaVuSansMono-Bold.ttf')}
SS = 3   # supersampling of shapes (smooth rounded corners)


@functools.lru_cache(maxsize=64)
def font(name, bold, px):
    files = FONT_FILES.get(name) or FONT_FILES['DejaVu Sans']
    for path in (os.path.join(FONT_DIR, files[1 if bold else 0]), os.path.join(FONT_DIR, files[0])):
        try:
            return ImageFont.truetype(path, px)
        except OSError:
            continue
    try:
        return ImageFont.load_default(px)
    except TypeError:   # Pillow before 10.1
        return ImageFont.load_default()


def rgb(value, default=(17, 25, 38)):
    try:
        n = int(str(value)[1:7], 16)
        return (n >> 16, (n >> 8) & 255, n & 255)
    except ValueError:
        return default


def on_color(value):
    r, g, b = rgb(value)
    return (17, 17, 17) if (r * 299 + g * 587 + b * 114) / 1000 > 160 else (255, 255, 255)


def wrap(text, fnt, width, max_lines):
    """Lines of text that fit the width; long words are broken, the last line ends with … when cut."""
    out = []
    for para in str(text or '').split('\n'):
        line = ''
        for word in para.split(' '):
            candidate = (line + ' ' + word).strip()
            if fnt.getlength(candidate) <= width:
                line = candidate
                continue
            if line:
                out.append(line)
            while fnt.getlength(word) > width and len(word) > 1:   # a word longer than the line
                cut = len(word)
                while cut > 1 and fnt.getlength(word[:cut]) > width:
                    cut -= 1
                out.append(word[:cut])
                word = word[cut:]
            line = word
        out.append(line)
    if len(out) > max_lines:
        out = out[:max_lines]
        last = out[-1]
        while last and fnt.getlength(last + '…') > width:
            last = last[:-1]
        out[-1] = last.rstrip() + '…'
    return [x for x in out if x] or ['']


def rounded_mask(size, radius):
    """An antialiased mask of a rounded rectangle."""
    w, h = size
    big = Image.new('L', (w * SS, h * SS), 0)
    ImageDraw.Draw(big).rounded_rectangle((0, 0, w * SS - 1, h * SS - 1), radius=max(0, radius * SS), fill=255)
    return big.resize((w, h), Image.LANCZOS)


def gradient(size, c1, c2, direction):
    w, h = size
    if direction not in ('vertical', 'horizontal', 'diagonal') or c1 == c2:
        return Image.new('RGB', size, c1)
    a, b = Image.new('RGB', size, c1), Image.new('RGB', size, c2)
    vertical = Image.linear_gradient('L').resize(size)              # dark at the top, light at the bottom
    horizontal = Image.linear_gradient('L').rotate(90).resize(size)  # dark on the left
    line = {'vertical': vertical, 'horizontal': horizontal}.get(direction) or ImageChops.add(vertical, horizontal, scale=2)
    return Image.composite(b, a, line)


def fit(img, size, cover):
    """The picture resized to the box: contain (whole picture) or cover (fills the box, cropped)."""
    w, h = size
    scale = (max if cover else min)(w / img.width, h / img.height)
    resized = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.LANCZOS)
    if not cover:
        return resized
    left, top = (resized.width - w) // 2, (resized.height - h) // 2
    return resized.crop((left, top, left + w, top + h))


def layout(cur, data, st, sw, sh, picture=None):
    """Sizes and positions of the notification on a screen sw x sh (independent of the background)."""
    scale = max(.5, min(3, float(data.get('scale') or 100) / 100))
    k = lambda key, default=100: max(.3, min(3, float(st.get(key, default) or default) / 100))
    title_px = max(14, int(sh / 38 * scale * k('title_size')))
    text_px = max(11, int(sh / 54 * scale * k('text_size')))
    small_px = max(10, int(sh / 72 * scale))
    pad = max(8, int(sh / 70 * scale * k('padding')))
    pos = data.get('position') or 'top-right'
    width = int(st.get('width') or 0)
    w = int(sw * width / 100) if width else int(min(sw * .9, max(320, sw * (.42 if pos in ('top', 'bottom', 'center') else .32) * scale)))
    banner = width >= 100
    radius = 0 if banner else int(float(st.get('radius') or 0) * sh / 1080 * scale)
    lv = (st.get('levels') or {}).get(cur.get('level')) or (st.get('levels') or {}).get('info') or {'color': '#3b82f6', 'icon': ''}
    fill = st.get('fill', 'stripe')
    stripe = int(max(6, pad * .6) * float(st.get('stripe', 100)) / 100) if fill == 'stripe' else 0
    stripe_pos = st.get('stripe_pos', 'left')
    border = max(2, int(pad * .3 * max(float(st.get('stripe', 100)), 30) / 100)) if fill == 'border' else 0
    image_pos = st.get('image_pos', 'left') if picture is not None and st.get('image') else ''
    img_w = int(w * float(st.get('image_size', 25)) / 100) if image_pos in ('left', 'right') else 0
    text_w = w - 2 * pad - (stripe if stripe_pos in ('left', 'right') else 0) - (img_w + pad if img_w else 0) - 2 * border
    title_font = font(st.get('font', 'DejaVu Sans'), bool(st.get('bold', True)), title_px)
    text_font = font(st.get('font', 'DejaVu Sans'), False, text_px)
    small_font = font(st.get('font', 'DejaVu Sans'), True, small_px)
    title = cur.get('title') or cur.get('message') or ''
    message = cur.get('message') if cur.get('title') else ''
    title_lines = wrap(title, title_font, max(40, text_w), 3)
    message_lines = wrap(message, text_font, max(40, text_w), 6) if message else []
    head = bool(st.get('icon', True) or st.get('source', True) or st.get('waiting', True))
    head_h = int(small_px * 1.5) if head else 0
    title_h = int(title_px * 1.22) * len(title_lines)
    text_h = int(text_px * 1.32) * len(message_lines)
    bar_h = max(3, int(pad * .35 * float(st.get('progress_size', 100)) / 100)) if st.get('progress', True) else 0
    img_top_h = int(w * float(st.get('image_size', 25)) / 100 * .45) if image_pos in ('top', 'bottom') else 0
    content_h = head_h + (int(pad * .3) if head else 0) + title_h + (int(pad * .4) + text_h if message_lines else 0)
    h = border * 2 + pad * 2 + content_h + (img_top_h + pad if img_top_h else 0) + (stripe if stripe_pos in ('top', 'bottom') else 0) + (bar_h + int(pad * .5) if bar_h else 0)
    if img_w:
        h = max(h, img_w + 2 * pad + 2 * border + (stripe if stripe_pos in ('top', 'bottom') else 0) + (bar_h + int(pad * .5) if bar_h else 0))
    margin = 0 if banner else int(min(sw, sh) * .035 * float(st.get('margin', 100)) / 100)
    shadow = 0 if banner else int(pad * 1.6 * float(st.get('shadow', 0)) / 100)
    x = sw - w - margin if pos.endswith('right') else margin if pos.endswith('left') else (sw - w) // 2
    bar_reserve = int(data.get('bar_height') or 0)
    y = margin if pos.startswith('top') else sh - h - margin - bar_reserve if pos.startswith('bottom') else (sh - h) // 2
    return {'x': x, 'y': y, 'w': w, 'h': h, 'pad': pad, 'radius': min(radius, h // 2, w // 2), 'shadow': shadow,
            'stripe': stripe, 'stripe_pos': stripe_pos, 'border': border, 'image_pos': image_pos, 'img_w': img_w,
            'img_top_h': img_top_h, 'level': lv, 'title_lines': title_lines, 'message_lines': message_lines,
            'title_font': title_font, 'text_font': text_font, 'small_font': small_font, 'title_px': title_px,
            'text_px': text_px, 'small_px': small_px, 'head': head, 'head_h': head_h, 'bar_h': bar_h, 'banner': banner}


def window_box(g, sw, sh):
    """The window around the notification (with room for the shadow), kept on the screen."""
    s = g['shadow']
    wx, wy = max(0, g['x'] - s), max(0, g['y'] - s)
    return wx, wy, min(sw, g['x'] + g['w'] + s), min(sh, g['y'] + g['h'] + s + s // 2)


def render(cur, data, st, g, box, background, picture=None):
    """The notification drawn into the screen picture `background` (the window area `box`).
    Returns (picture RGB, progress bar (x0, y0, x1, y1) in window coordinates, bar colour, track colour)."""
    wx, wy, wx1, wy1 = box
    W, H = wx1 - wx, wy1 - wy
    base = background.convert('RGBA').resize((W, H)) if background.size != (W, H) else background.convert('RGBA')
    cx, cy, cw, ch = g['x'] - wx, g['y'] - wy, g['w'], g['h']
    r = g['radius']
    lv = g['level']
    color = rgb(lv.get('color'), (59, 130, 246))
    fill = st.get('fill', 'stripe')
    solid = fill == 'solid'
    mask = rounded_mask((cw, ch), r)

    if g['shadow']:
        sh_layer = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        alpha = Image.new('L', (W, H), 0)
        alpha.paste(mask.point(lambda v: int(v * .55 * float(st.get('shadow', 0)) / 100)), (cx, cy + g['shadow'] // 3))
        sh_layer.putalpha(alpha.filter(ImageFilter.GaussianBlur(max(1, g['shadow'] * .55))))
        base = Image.alpha_composite(base, sh_layer)

    # the card: what is behind it (blurred for glass), then its colour or gradient with the chosen opacity
    region = base.crop((cx, cy, cx + cw, cy + ch))
    blur = float(st.get('blur', 0) or 0)
    if blur:
        region = region.filter(ImageFilter.GaussianBlur(blur * ch / 300))
    c1 = color if solid else rgb(st.get('bg'))
    c2 = tuple(max(0, int(v * .72)) for v in color) if solid and st.get('gradient', 'none') != 'none' else rgb(st.get('bg2') or st.get('bg'))
    layer = gradient((cw, ch), c1, c2, st.get('gradient', 'none')).convert('RGBA')
    layer.putalpha(int(255 * float(st.get('bg_opacity', 100)) / 100))
    card = Image.alpha_composite(region, layer)
    if g['image_pos'] == 'background' and picture is not None:
        pic = fit(picture.convert('RGBA'), (cw, ch), True)
        dim = int(255 * float(st.get('image_dim', 45)) / 100)
        card = Image.alpha_composite(card, pic)
        card = Image.alpha_composite(card, Image.new('RGBA', (cw, ch), c1 + (dim,)))
    draw = ImageDraw.Draw(card)
    bw = g['border']
    if g['stripe']:
        s, p = g['stripe'], g['stripe_pos']
        rect = {'left': (0, 0, s, ch), 'right': (cw - s, 0, cw, ch), 'top': (0, 0, cw, s), 'bottom': (0, ch - s, cw, ch)}[p if p in ('left', 'right', 'top', 'bottom') else 'left']
        draw.rectangle(rect, fill=color + (255,))
    base.paste(card, (cx, cy), mask)
    if bw:
        ring = Image.new('RGBA', (cw * SS, ch * SS), (0, 0, 0, 0))
        border_color = rgb(st.get('border_color')) if st.get('border_color') else color
        ImageDraw.Draw(ring).rounded_rectangle((bw * SS // 2, bw * SS // 2, cw * SS - bw * SS // 2 - 1, ch * SS - bw * SS // 2 - 1),
                                               radius=max(0, (r - bw // 2) * SS), outline=border_color + (255,), width=bw * SS)
        ring = ring.resize((cw, ch), Image.LANCZOS)
        base.alpha_composite(ring, (cx, cy))

    # content
    d = ImageDraw.Draw(base)
    pad = g['pad']
    left = cx + bw + pad + (g['stripe'] if g['stripe_pos'] == 'left' else 0)
    right = cx + cw - bw - pad - (g['stripe'] if g['stripe_pos'] == 'right' else 0)
    top = cy + bw + pad + (g['stripe'] if g['stripe_pos'] == 'top' else 0)
    # where the content ends: the stripe at the bottom and a countdown line under the text are kept free
    bottom = cy + ch - bw - pad - (g['stripe'] if g['stripe_pos'] == 'bottom' else 0) \
        - (g['bar_h'] + int(pad * .5) if g['bar_h'] and st.get('progress_pos', 'bottom') != 'top' else 0)
    if g['bar_h'] and st.get('progress_pos', 'bottom') == 'top':
        top += g['bar_h'] + int(pad * .5)
    if picture is not None and g['image_pos'] in ('left', 'right', 'top', 'bottom'):
        shape = st.get('image_shape', 'rounded')
        if g['image_pos'] in ('top', 'bottom'):
            box_w, box_h = right - left, g['img_top_h']
            pic = fit(picture.convert('RGBA'), (box_w, box_h), True)
            radius = 0 if shape == 'square' else box_h // 2 if shape == 'circle' else max(8, r // 2)
            mask = Image.composite(pic.getchannel('A'), Image.new('L', pic.size, 0), rounded_mask(pic.size, radius))
            if g['image_pos'] == 'top':
                base.paste(pic, (left, top), mask)
                top += box_h + pad
            else:
                base.paste(pic, (left, bottom - box_h), mask)
        else:
            size = g['img_w']
            if shape == 'circle':   # a round picture fills its circle (cropped), a logo keeps its proportions
                pic = fit(picture.convert('RGBA'), (size, size), True)
                circle = Image.new('L', (size * SS, size * SS), 0)
                ImageDraw.Draw(circle).ellipse((0, 0, size * SS - 1, size * SS - 1), fill=255)
                pic.putalpha(Image.composite(pic.getchannel('A'), Image.new('L', pic.size, 0), circle.resize((size, size), Image.LANCZOS)))
            else:
                pic = fit(picture.convert('RGBA'), (size, size), False)
                if shape == 'rounded':
                    pic.putalpha(Image.composite(pic.getchannel('A'), Image.new('L', pic.size, 0),
                                                 rounded_mask(pic.size, int(min(pic.size) * .18))))
            px = left if g['image_pos'] == 'left' else right - pic.width
            room = max(0, bottom - top - pic.height)
            py = top + {'top': 0, 'bottom': room}.get(st.get('image_align', 'center'), room // 2)
            base.alpha_composite(pic, (px, py))
            if g['image_pos'] == 'left':
                left += size + pad
            else:
                right -= size + pad
    fg = on_color(lv.get('color')) if solid else rgb(st.get('title'), (248, 250, 252))
    fg2 = on_color(lv.get('color')) if solid else rgb(st.get('text'), (203, 213, 225))
    fgm = on_color(lv.get('color')) if solid else rgb(st.get('muted'), (148, 163, 184))
    center = st.get('align') == 'center'
    width = right - left

    def line(text, y, fnt, fill_color):
        x = left + (width - fnt.getlength(text)) / 2 if center else left
        d.text((x, y), text, font=fnt, fill=fill_color)

    y = top
    if g['head']:
        label = ((lv.get('icon') or '') + '  ' if st.get('icon', True) and lv.get('icon') else '') + ((cur.get('source') or 'CARACAL') if st.get('source', True) else '')
        line(label.strip(), y, g['small_font'], fg if solid else color)
        waiting = int(data.get('waiting') or 0)
        if waiting and st.get('waiting', True) and not center:
            t = f'+{waiting}'
            d.text((right - g['small_font'].getlength(t), y), t, font=g['small_font'], fill=fgm)
        y += g['head_h'] + int(pad * .3)
    for text in g['title_lines']:
        line(text, y, g['title_font'], fg)
        y += int(g['title_px'] * 1.22)
    if g['message_lines']:
        y += int(pad * .4)
        for text in g['message_lines']:
            line(text, y, g['text_font'], fg2)
            y += int(g['text_px'] * 1.32)

    bar, bar_color, track = None, None, None
    if g['bar_h']:
        bar_color = '#%02x%02x%02x' % (fg if solid else color)
        track_rgb = tuple(int(a * .75 + b * .25) for a, b in zip(rgb(st.get('bg')), rgb(st.get('muted'), (148, 163, 184)))) if not solid else tuple(int(v * .6) for v in color)
        track = '#%02x%02x%02x' % track_rgb
        if r:   # rounded corners: the bar sits inside the card, clear of the corners
            inset = max(r // 2, pad)
            y0 = cy + ch - bw - int(pad * .5) - g['bar_h'] if st.get('progress_pos', 'bottom') != 'top' else cy + bw + int(pad * .35)
            bar = (cx + inset, y0, cx + cw - inset, y0 + g['bar_h'])
        else:
            y0 = cy + ch - bw - g['bar_h'] if st.get('progress_pos', 'bottom') != 'top' else cy + bw
            bar = (cx + bw + (g['stripe'] if g['stripe_pos'] == 'left' else 0), y0, cx + cw - bw, y0 + g['bar_h'])
        d.rectangle(bar, fill=track)
    out = base.convert('RGB')
    opacity = float(st.get('opacity', 100) or 100)
    if opacity < 100:   # the whole notification see-through (blended with what is behind it)
        out = Image.blend(background.convert('RGB').resize(out.size), out, opacity / 100)
    return out, bar, bar_color, track


def png(img):
    """PNG bytes for Tk's PhotoImage (Tk 8.6 reads PNG itself, no ImageTk needed)."""
    buf = io.BytesIO()
    img.save(buf, 'PNG', compress_level=1)
    return buf.getvalue()


def grab(box):
    """The screen inside box (x0, y0, x1, y1), or None when it cannot be read."""
    try:
        from PIL import ImageGrab
        return ImageGrab.grab(bbox=box).convert('RGB')
    except Exception:   # no X display access, Pillow without XCB
        return None


def screenshot(max_width=1280, quality=72):
    """The whole screen as JPEG bytes (what the TV shows, notifications included), or None."""
    try:
        from PIL import ImageGrab
        img = ImageGrab.grab().convert('RGB')
    except Exception:
        return None
    if img.width > max_width:
        img = img.resize((max_width, int(img.height * max_width / img.width)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, 'JPEG', quality=quality, optimize=True)
    return buf.getvalue()
