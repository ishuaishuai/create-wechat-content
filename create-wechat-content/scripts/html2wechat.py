#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
html2wechat.py —— 通用 HTML → 微信公众号可粘贴版转换器（纯标准库，无依赖）

用法:
    python3 html2wechat.py input.html output.html [--md] [--fragment] [--check]
                           [--accent #d2601f] [--map bar=data-row,stat-row]

为什么需要:
微信编辑器会吞掉 <style>、class、flex/grid、伪元素(::before/::after)、
position、部分渐变。只剩内联 style + table 布局能活下来。

通用性设计（重要）:
本转换器分两层——

  【内核层】完全通用，与具体稿子无关:
    1. HTML 解析 / CSS 解析(@media 剥离、:root 变量、选择器拆分)
    2. 按选择器特异性把规则内联到每个元素
    3. flex/grid -> block，position/transform/动画等不稳定属性剔除
    4. 渐变背景 -> 纯色
    5. ::before/::after 的 content 字符 -> 实体化成真实可见 span
    6. div -> section，输出干净的 body 片段

  【组件层】按"结构+样式特征"自动探测，不依赖类名:
    - 标签条: 原 flex/grid 容器 + 子元素全是短文本叶子 -> inline-block 换行
    - 数据条: 有 width:xx% 的宽度块 + 灰底轨道 -> 合成三列表格
    - 时间轴: 多个子项带 border-left -> 竖线 + 真实圆点字符
    - 金句卡: 类名 hint 或伪元素大引号 -> 真实引号字符
    探测不到就安全降级为普通块，绝不会吞内容。

  类名只是"提示"(CLASS_HINTS)，不是判定条件。换任何模板/类名都能工作，
  如有特殊组件可用 --map 追加自定义类名提示，或在 transform() 里加钩子。

作者: 包子 🥟 / 鱼叔的创作流水线
"""

import re
import sys
import html as html_mod
from html.parser import HTMLParser
from collections import Counter

# ---------------------------------------------------------------- 常量

VOID = {'meta', 'br', 'img', 'hr', 'input', 'link', 'col', 'source', 'wbr'}

# 微信里会被清掉或不稳定的样式属性
DROP_PROPS = {
    'position', 'top', 'right', 'bottom', 'left', 'z-index', 'float',
    'transform', 'transition', 'animation', 'cursor', 'overflow',
    'box-sizing', 'flex', 'flex-wrap', 'flex-direction', 'align-items',
    'justify-content', 'gap', 'order', 'grid', 'grid-template-columns',
    'content', 'counter-reset', 'list-style-position',
    # 注：white-space 不在此列 —— <pre> 代码块靠它保住缩进
    # 微信 X5 Blink 不支持的合成/混合/裁剪效果，一律剔除
    'backdrop-filter', 'mix-blend-mode', 'clip-path', 'mask', 'mask-image',
    'will-change', 'contain', 'isolation', 'perspective', 'backface-visibility',
}

# 伪元素样式里允许继承到实体化 span 上的属性
SAFE_PSEUDO_PROPS = {
    'color', 'font-size', 'font-family', 'font-weight', 'line-height',
    'margin', 'margin-right', 'margin-left', 'margin-top', 'margin-bottom',
    'vertical-align', 'opacity', 'display', 'letter-spacing',
}

GRADIENT_RE = re.compile(r'(linear|radial)-gradient\s*\(')

# 可从 html/body/* 全局规则继承到外层 wrapper 的属性
INHERITABLE_PROPS = {
    'font-family', 'font-size', 'font-weight', 'font-style', 'font-variant',
    'color', 'line-height', 'letter-spacing', 'word-spacing', 'text-align',
    'text-indent', 'word-break', 'overflow-wrap', 'text-transform',
    'background', 'background-color',
}
COLOR_RE = re.compile(r'(#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)|transparent)')

# 组件类名"提示"——命中只是加分项，绝不作为唯一判定条件。
# 可用 --map 追加，如 --map bar=data-row,stat-row
CLASS_HINTS = {
    'pillbar': ('tagbar', 'tag-bar', 'tag-list', 'taglist', 'tags', 'chips',
                'pill', 'badges', 'keywords', 'meta-tags', 'labels'),
    'bar': ('bar-row', 'barrow', 'progress', 'stat-row', 'rate-row',
            'chart-row', 'data-row', 'meter', 'rating'),
    # 注意：hint 是「类名子串匹配」，别放太短的词（'tl' 会误伤 tl-item/tl-year）
    'timeline': ('timeline', 'tl-list', 'tl-wrap', 'tl-group', 'roadmap',
                 'milestones', 'history', 'steps'),
    'quote': ('quote', 'pullquote', 'quote-card', 'golden', 'highlight-quote'),
}

PAGE_WRAPPER = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} · 微信粘贴版</title>
<style>
  body{{margin:0;padding:0;background:#eceff1;font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;}}
  .bar{{position:sticky;top:0;z-index:9;background:#263238;color:#fff;padding:12px 20px;
        display:flex;align-items:center;gap:14px;flex-wrap:wrap;font-size:13px;box-shadow:0 2px 10px rgba(0,0,0,.15);}}
  .bar b{{font-size:15px;}}
  .bar .tip{{color:#b0bec5;line-height:1.7;}}
  .btn{{background:#07c160;color:#fff;border:0;padding:9px 22px;border-radius:6px;
        font-size:14px;font-weight:700;cursor:pointer;}}
  .btn:active{{background:#06ad56;}}
  .ok{{color:#69f0ae;opacity:0;transition:opacity .3s;}}
  .wrap{{max-width:414px;margin:22px auto 60px;background:#fff;
         box-shadow:0 4px 20px rgba(0,0,0,.12);overflow:hidden;}}
</style>
</head>
<body>
<div class="bar">
  <b>微信粘贴版</b>
  <span class="tip">① 点「复制到公众号」② 进公众号后台编辑器 Ctrl/⌘+V ③ 图片需单独上传素材库</span>
  <button class="btn" id="cp">复制到公众号</button>
  <span class="ok" id="ok">已复制 ✓</span>
</div>
<div class="wrap" id="src">{content}</div>
<script>
var btn=document.getElementById('cp'),ok=document.getElementById('ok'),tm=null;
function done(){{ok.style.opacity=1;clearTimeout(tm);tm=setTimeout(function(){{ok.style.opacity=0;}},2200);}}
function legacyCopy(){{
  var n=document.getElementById('src'),r=document.createRange();
  r.selectNodeContents(n);
  var s=window.getSelection();s.removeAllRanges();s.addRange(r);
  var okk=false;
  try{{okk=document.execCommand('copy');}}catch(e){{okk=false;}}
  s.removeAllRanges();
  if(okk){{done();}}else{{alert('复制失败，请手动 Ctrl/⌘+A 选中正文后 Ctrl/⌘+C');}}
}}
btn.onclick=async function(){{
  var n=document.getElementById('src');
  /* 优先 ClipboardItem 写 text/html（保留样式），execCommand 兜底 */
  if(navigator.clipboard&&window.ClipboardItem){{
    try{{
      await navigator.clipboard.write([new ClipboardItem({{
        'text/html':new Blob([n.innerHTML],{{type:'text/html'}}),
        'text/plain':new Blob([n.innerText],{{type:'text/plain'}})
      }})]);
      done();return;
    }}catch(e){{}}
  }}
  legacyCopy();
}};
</script>
</body>
</html>
"""


# ---------------------------------------------------------------- DOM

class Node:
    def __init__(self, tag, attrs=None, parent=None):
        self.tag = tag
        self.attrs = attrs or {}
        self.style = {}      # 最终输出的内联样式
        self.orig = {}       # 原始 CSS 计算值（供组件探测用，不输出）
        self.force = {}      # 结构重写写入的样式，优先级最高
        self.parent = parent
        self.children = []   # Node | str

    @property
    def classes(self):
        return self.attrs.get('class', '').split()

    def tagname(self):
        return self.tag.lower()


class DomBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node('__root__')
        self.cur = self.root
        self.style_blocks = []
        self._grab = False

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == 'style':
            self._grab = True
            self.style_blocks.append('')
            return
        n = Node(tag, dict(attrs), self.cur)
        self.cur.children.append(n)
        if tag not in VOID:
            self.cur = n

    def handle_startendtag(self, tag, attrs):
        """自闭合标签：<rect/>、<path/> 等只建节点，绝不入栈。
        入栈会让后续兄弟全部挂进去，把 SVG 子树搅成嵌套乱麻。"""
        tag = tag.lower()
        if tag == 'style':
            return
        n = Node(tag, dict(attrs), self.cur)
        self.cur.children.append(n)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == 'style':
            self._grab = False
            return
        node = self.cur
        while node is not None and node.tagname() != tag:
            node = node.parent
        if node is not None and node.parent is not None:
            self.cur = node.parent

    def handle_data(self, data):
        if self._grab:
            self.style_blocks[-1] += data
            return
        self.cur.children.append(data)


# ---------------------------------------------------------------- CSS

def strip_at_rules(css):
    """去掉 @media/@font-face 等块（微信不支持，且会干扰规则解析）"""
    out, i, n = [], 0, len(css)
    while i < n:
        if css[i] == '@':
            j = css.find('{', i)
            if j == -1:
                break
            depth, k = 0, j
            while k < n:
                if css[k] == '{':
                    depth += 1
                elif css[k] == '}':
                    depth -= 1
                    if depth == 0:
                        break
                k += 1
            i = k + 1
            continue
        out.append(css[i])
        i += 1
    return ''.join(out)


def parse_css(css):
    """返回 (rules, variables, pseudo_rules, base)"""
    css = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    css = strip_at_rules(css)
    rules, variables, pseudo_rules, base_rules = [], {}, [], {}
    for m in re.finditer(r'([^{}]+)\{([^{}]*)\}', css):
        sel = m.group(1).strip()
        decls = {}
        for d in m.group(2).split(';'):
            if ':' not in d:
                continue
            k, v = d.split(':', 1)
            k, v = k.strip().lower(), v.strip()
            if not v:
                continue
            decls[k] = v
        if sel.startswith(':root'):
            for k, v in decls.items():
                if k.startswith('--'):
                    variables[k] = v
            continue
        if sel in ('*', 'html', 'html *'):
            continue
        for part in sel.split(','):
            part = part.strip()
            if not part:
                continue
            # 属性选择器：[data-x] 无法求值，只能剥离；
            # 剥离后若什么都不剩（裸 [data-x]），整条规则丢弃——否则会匹配全文档
            if part == '*':
                base_rules.update(decls)
                continue
            if not part:
                continue
            # html/body/* 的全局样式：不参与元素匹配，收进 base 交给外层 wrapper 继承
            # （否则 *_ {font-family} 这类最常见的全局字体定义会整体丢失）
            if part in ('html', 'body', 'html body', 'body *'):
                base_rules.update(decls)
                continue
            if '::' in part:
                # 伪元素规则单独收集，后面实体化
                base, _, ps = part.partition('::')
                base = base.strip()
                if base:
                    pseudo_rules.append((base, '::' + ps.strip(), decls))
                continue
            rules.append((part, decls))
    return rules, variables, pseudo_rules, base_rules


def resolve_vars(value, variables):
    def rep(m):
        return variables.get(m.group(1), m.group(2) if m.lastindex > 1 else '')
    for _ in range(6):
        nv = re.sub(r'var\(\s*(--[\w-]+)\s*(?:,)?\s*([^)]*)\)', rep, value)
        if nv == value:
            break
        value = nv
    return value


FONT_SHORTHAND_RE = re.compile(
    r'^\s*(italic|oblique)?\s*(small-caps)?\s*'
    r'(normal|bold|bolder|lighter|[1-9]00)?\s+'
    r'([\d.]+(?:px|pt|rem|em|%)?)(?:\s*/\s*([\d.]+|normal))?\s+(.+)$')

BG_JUNK_RE = re.compile(
    r'\b(no-repeat|repeat|repeat-x|repeat-y|scroll|fixed|local|center|top|'
    r'bottom|left|right|cover|contain|auto)\b|/\s*[\d.]+(?:px|%|auto)?')


def expand_font_shorthand(v):
    """font: italic 700 16px/1.8 Georgia -> 各独立属性（微信对 font 简写支持差）"""
    m = FONT_SHORTHAND_RE.match(v)
    if not m:
        return None
    out = {}
    if m.group(1):
        out['font-style'] = m.group(1)
    if m.group(2):
        out['font-variant'] = m.group(2)
    if m.group(3):
        out['font-weight'] = {'normal': '400', 'bold': '700'}.get(
            m.group(3), m.group(3))
    size = m.group(4)
    out['font-size'] = size if re.search(r'\d', size) else size + 'px'
    if m.group(5):
        out['line-height'] = m.group(5)
    out['font-family'] = m.group(6).strip()
    return out


def hsl_to_rgb(v):
    m = re.match(r'hsla?\(\s*([\d.]+)\s*,\s*([\d.]+)%\s*,\s*([\d.]+)%\s*'
                 r'(?:,\s*([\d.]+)\s*)?\)', v.strip())
    if not m:
        return None
    h = float(m.group(1))
    s, l = float(m.group(2)) / 100, float(m.group(3)) / 100
    a = float(m.group(4)) if m.group(4) else 1.0
    c = (1 - abs(2 * l - 1)) * s
    x = c * (1 - abs((h / 60) % 2 - 1))
    m0 = l - c / 2
    r, g, b = [(c, x, 0), (x, c, 0), (0, c, x),
               (0, x, c), (x, 0, c), (c, 0, x)][int(h // 60) % 6]
    r, g, b = [max(0, min(255, round((t + m0) * 255))) for t in (r, g, b)]
    return 'rgba(%d,%d,%d,%g)' % (r, g, b, a) if a < 1 else 'rgb(%d,%d,%d)' % (r, g, b)


def hex8_to_rgba(v):
    m = re.match(r'^#([0-9a-fA-F]{8})$', v.strip())
    if not m:
        return None
    r, g, b, a = [int(m.group(1)[i:i + 2], 16) for i in (0, 2, 4, 6)]
    return 'rgba(%d,%d,%d,%g)' % (r, g, b, round(a / 255, 2))


def degrade_func_value(v):
    """calc()/clamp()/min()/max()/vw/vh —— 微信支持度差，降级为普通值"""
    def take_first(expr):
        nums = re.findall(r'[\d.]+(?:px|%|em|rem|vw|vh|pt)?', expr)
        return nums[0] if nums else '100%'
    m = re.search(r'calc\(\s*([^()]*(?:\([^()]*\)[^()]*)*)\)', v)
    if m:
        v = v[:m.start()] + take_first(m.group(1)) + v[m.end():]
    m = re.search(r'clamp\(\s*([^,]+),\s*([^,]+),\s*([^)]+)\)', v)
    if m:
        v = v[:m.start()] + m.group(2).strip() + v[m.end():]
    for fn in ('min', 'max'):
        m = re.search(r'\b%s\(\s*([^,()]+)' % fn, v)
        if m:
            v = v[:m.start()] + m.group(1).strip() + v[m.end():]
    v = re.sub(r'([\d.]+)vw', lambda m: '%gpx' % (float(m.group(1)) * 3.75), v)
    v = re.sub(r'([\d.]+)vh', lambda m: '%gpx' % (float(m.group(1)) * 6), v)
    return v


def sanitize_props(props):
    """统一清洗声明值：!important / 函数值 / 颜色格式 / 背景图片 / font 简写"""
    for k in list(props.keys()):
        v = props.get(k)
        if not isinstance(v, str):
            continue
        v = re.sub(r'\s*!important\s*', '', v, flags=re.I)
        v = degrade_func_value(v)
        v = hex8_to_rgba(v) or v
        v = hsl_to_rgb(v) or v
        if k in ('background', 'background-image', 'background-color'):
            if 'url(' in v:
                v = re.sub(r'url\([^)]*\)', '', v)
                v = BG_JUNK_RE.sub('', v)
                cols = [c for c in COLOR_RE.findall(v) if c != 'transparent']
                v = cols[0] if cols else ''
            v = v.strip()
            if not v:
                props.pop(k, None)
                continue
        props[k] = v.strip()
    if 'font' in props:
        exp = expand_font_shorthand(props.pop('font'))
        if exp:
            for kk, vv in exp.items():
                props.setdefault(kk, vv)


def degrade_background(props):
    """把渐变背景降级为纯色"""
    for key in ('background', 'background-image'):
        val = props.get(key)
        if not val or not GRADIENT_RE.search(val):
            continue
        colors = [c for c in COLOR_RE.findall(val) if c != 'transparent']
        if not colors:
            del props[key]
            continue
        props['background'] = colors[-1]
        props.pop('background-image', None)
        break


SIMPLE_RE = re.compile(r'(?P<tag>^[a-zA-Z][\w-]*)?|(?P<cls>\.[\w-]+)|(?P<id>#[A-Za-z][\w-]*)|(?P<pseudo>:[\w-]+(?:\([^)]*\))?)')


def parse_simple(part):
    info = {'tag': None, 'classes': [], 'id': None, 'pseudo': []}
    for m in SIMPLE_RE.finditer(part):
        if m.group('tag') and not info['tag']:
            info['tag'] = m.group('tag').lower()
        elif m.group('cls'):
            info['classes'].append(m.group('cls')[1:])
        elif m.group('id'):
            info['id'] = m.group('id')[1:]
        elif m.group('pseudo'):
            info['pseudo'].append(m.group('pseudo'))
    if part.startswith('*'):
        info['tag'] = None
    return info


NTH_RE = re.compile(r':nth-child\((\d+|even|odd)\)')


def _sib_index(node):
    sibs = [c for c in (node.parent.children if node.parent else [])
            if isinstance(c, Node)]
    try:
        return sibs, sibs.index(node)
    except ValueError:
        return sibs, -1


def pseudo_ok(node, pseudo):
    for p in pseudo:
        base = p.split('(')[0]
        if base == ':first-child':
            sibs, i = _sib_index(node)
            if i != 0:
                return False
        elif base == ':last-child':
            sibs, i = _sib_index(node)
            if i != len(sibs) - 1:
                return False
        elif base == ':not':
            inner = p[p.index('(') + 1:p.rindex(')')] if '(' in p else ''
            if simple_matches(node, parse_simple(inner)):
                return False
        elif base == ':nth-child':
            sibs, i = _sib_index(node)
            m = NTH_RE.match(p)
            if not m:
                return False
            v, idx = m.group(1), i + 1
            if v == 'even':
                ok = idx % 2 == 0
            elif v == 'odd':
                ok = idx % 2 == 1
            else:
                ok = idx == int(v)
            if not ok:
                return False
        else:
            # :hover 等交互伪类 -> 一律不匹配（微信里也不存在）
            return False
    return True


def simple_matches(node, info):
    if info['tag'] and node.tagname() != info['tag']:
        return False
    for c in info['classes']:
        if c not in node.classes:
            return False
    if info['id'] and node.attrs.get('id') != info['id']:
        return False
    if info['pseudo'] and not pseudo_ok(node, info['pseudo']):
        return False
    return True


def tokenize_selector(sel):
    """拆成 [(combinator, simple), ...]，combinator: None(后代) / '>' / '+' / '~'"""
    out, comb = [], None
    for tok in re.split(r'(\s*[>+~]\s*|\s+)', sel.strip()):
        t = tok.strip()
        if not t:
            continue
        if t in ('>', '+', '~'):
            comb = t
            continue
        out.append((comb, t))
        comb = None
    return out


def selector_matches(node, selector):
    """从右往左精确匹配，支持 后代 / > / + / ~ 四种组合器"""
    toks = tokenize_selector(selector)
    if not toks:
        return False
    if not simple_matches(node, parse_simple(toks[-1][1])):
        return False
    cur = node
    # 组合器由右侧那个 token 携带（.s1 + .s2 里 '+' 属于 .s2），
    # 向左回溯时要取 toks[j+1] 的，取自己的会把 + ~ 误判成后代
    for j in range(len(toks) - 2, -1, -1):
        comb, simple = toks[j + 1][0], toks[j][1]
        info = parse_simple(simple)
        if comb == '>':
            cur = cur.parent
            if cur is None or not simple_matches(cur, info):
                return False
        elif comb in ('+', '~'):
            sibs, i = _sib_index(cur)
            if i < 0:
                return False
            if comb == '+':
                if i == 0 or not simple_matches(sibs[i - 1], info):
                    return False
                cur = sibs[i - 1]
            else:
                hit = next((s for s in reversed(sibs[:i])
                            if simple_matches(s, info)), None)
                if hit is None:
                    return False
                cur = hit
        else:  # 后代
            p, hit = cur.parent, None
            while p is not None:
                if simple_matches(p, info):
                    hit = p
                    break
                p = p.parent
            if hit is None:
                return False
            cur = hit
    return True


def specificity(selector):
    a = b = c = 0
    for _, simple in tokenize_selector(selector):
        info = parse_simple(simple)
        if info['id']:
            a += 1
        b += len(info['classes']) + len(info['pseudo'])
        if info['tag']:
            c += 1
    return (a, b, c)


def all_nodes(root):
    out = []

    def walk(n):
        for c in n.children:
            if isinstance(c, Node):
                out.append(c)
                walk(c)
    walk(root)
    return out


def precompute_orig(root, rules_list, variables):
    """算出每个节点的原始 CSS 声明，供组件探测使用（不输出）"""
    for n in all_nodes(root):
        acc = {}
        for sel, decls in rules_list:
            if selector_matches(n, sel):
                for k, v in decls.items():
                    acc[k] = resolve_vars(v, variables)
        own = n.attrs.get('style', '')
        for d in own.split(';'):
            if ':' in d:
                k, v = d.split(':', 1)
                acc[k.strip().lower()] = v.strip()
        # 继承父级字号/颜色有助于判断，但不参与 display 判定
        n.orig = acc


def inline_styles(root, rules_list, variables):
    for node in all_nodes(root):
        hits = []
        for i, (sel, decls) in enumerate(rules_list):
            if selector_matches(node, sel):
                hits.append((specificity(sel), i, decls))
        hits.sort(key=lambda x: (x[0], x[1]))
        acc = dict(node.style)
        for _, _, decls in hits:
            for k, v in decls.items():
                acc[k] = resolve_vars(v, variables)
        own = node.attrs.get('style', '')
        for d in own.split(';'):
            if ':' in d:
                k, v = d.split(':', 1)
                acc[k.strip().lower()] = v.strip()
        degrade_background(acc)
        sanitize_props(acc)
        node.style = {}
        for k, v in acc.items():
            if k in DROP_PROPS:
                continue
            if k == 'display' and v in ('flex', 'inline-flex', 'grid', 'inline-grid'):
                v = 'block'
            node.style[k] = v
        # 结构重写阶段写入的样式优先级最高，放在最后
        node.style.update(node.force)


# ---------------------------------------------------------------- 工具

def children_nodes(node):
    return [c for c in node.children if isinstance(c, Node)]


def text_of(node):
    parts = []
    for c in node.children:
        if isinstance(c, Node):
            parts.append(text_of(c))
        else:
            parts.append(c)
    return ''.join(parts)


def make(tag, style=None, attrs=None, children=None):
    n = Node(tag, attrs or {})
    n.style = dict(style or {})
    n.children = children or []
    for c in n.children:
        if isinstance(c, Node):
            c.parent = n
    return n


def table(rows, style=None, widths=None):
    """rows: [[cell_node_or_text, ...], ...]，生成微信友好的 table"""
    t = make('table', dict(style or {}, **{
        'width': '100%', 'border-collapse': 'collapse', 'font-size': '14px'}))
    t.attrs['cellspacing'] = '0'
    t.attrs['cellpadding'] = '0'
    for ri, row in enumerate(rows):
        tr = make('tr')
        for ci, cell in enumerate(row):
            td = make('td', {'border': '1px solid #e0ddd8', 'padding': '9px 10px',
                             'vertical-align': 'top'})
            if isinstance(cell, Node):
                td.children = [cell]
                cell.parent = td
            else:
                td.children = [str(cell)]
            if widths and ci < len(widths) and widths[ci]:
                td.style['width'] = widths[ci]
            tr.children.append(td)
            td.parent = tr
        t.children.append(tr)
        tr.parent = t
    return t


def notice_block(text, title=None):
    """灰色虚线提示块：微信不支持的内容统一降级形态"""
    box = make('section', {
        'margin': '16px 0', 'padding': '14px', 'border': '2px dashed #cbd5e1',
        'border-radius': '8px', 'background': '#f8fafc', 'text-align': 'center',
        'font-size': '13px', 'color': '#64748b', 'line-height': '1.6'})
    box.children = [str(title or '⚠️ '), str(text)]
    return box


def hint(classes, key):
    """类名提示命中（只是加分项）"""
    s = ' '.join(classes).lower()
    return any(h in s for h in CLASS_HINTS[key])


def was_flex(node):
    return node.orig.get('display', '').startswith(('flex', 'inline-flex', 'grid', 'inline-grid'))


def own_style(node):
    """元素自身 style 属性 -> dict"""
    out = {}
    for d in (node.attrs.get('style', '') or '').split(';'):
        if ':' in d:
            k, v = d.split(':', 1)
            out[k.strip().lower()] = v.strip()
    return out


def first_color(node, keys=('background', 'background-color'), default=None):
    """取节点背景色：先看 CSS 规则，再看自身 style 属性"""
    own = own_style(node)
    for k in keys:
        for src in (node.orig.get(k), own.get(k)):
            m = COLOR_RE.search(src or '')
            if m and m.group(1).lower() != 'transparent':
                return m.group(1)
    return default


def guess_accent(root, variables, fallback='#d2601f'):
    """从 :root 变量或全稿最高频饱和色推断主色"""
    for k in ('--accent', '--primary', '--brand', '--main', '--gold',
              '--theme', '--highlight', '--color-accent', '--brand-color'):
        if k in variables:
            v = resolve_vars(variables[k], variables).strip()
            if v.startswith('#'):
                return v
    cnt = Counter()
    for n in all_nodes(root):
        for key in ('background', 'background-color', 'color', 'border-left-color'):
            v = n.orig.get(key, '')
            m = re.match(r'#([0-9a-fA-F]{6})', v.strip())
            if not m:
                continue
            h = m.group(1)
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            if max(r, g, b) - min(r, g, b) < 45:     # 灰/白/黑跳过
                continue
            cnt['#' + h.lower()] += 1
    return cnt.most_common(1)[0][0] if cnt else fallback


# ---------------------------------------------------------------- 组件探测（通用，不依赖类名）

def is_leaf_text(node):
    return not children_nodes(node)


def looks_like_pill(node):
    """标签条里的单个标签：叶子 + 短文本 + 有装饰(padding/border/背景/圆角)"""
    if not is_leaf_text(node):
        return False
    t = text_of(node).strip()
    if not t or len(t) > 20:
        return False
    return any(k in node.orig for k in ('padding', 'border-radius', 'background',
                                        'background-color', 'border'))


def transform_pillbars(root):
    """【标签条】原 flex/grid 容器 + 子元素全是短文本装饰块 -> inline-block 换行排列"""
    for n in all_nodes(root):
        kids = children_nodes(n)
        if len(kids) < 2:
            continue
        if not (was_flex(n) or hint(n.classes, 'pillbar')):
            continue
        if not all(looks_like_pill(k) for k in kids):
            continue
        for k in kids:
            k.force['display'] = 'inline-block'
            k.force['margin'] = '0 8px 8px 0'
        n.force.setdefault('line-height', '1.9')


def _leaf_texts(node, exclude=()):
    """按文档序收集节点下所有叶子文本（跳过 exclude 里的节点）"""
    res = []

    def walk(x):
        for c in x.children:
            if isinstance(c, str):
                if c.strip():
                    res.append(c.strip())
            elif c not in exclude:
                walk(c)
    walk(node)
    return res


def transform_bars(root, accent):
    """【数据条】探测 width:xx% 的宽度块 -> 灰底槽 + 彩条，同组合成三列表格"""
    fills = []
    for n in all_nodes(root):
        if children_nodes(n):
            continue                                   # 必须是空叶子块
        pct = None
        for src in (n.attrs.get('style', ''), n.orig.get('width', '')):
            m = re.search(r'width\s*:\s*([\d.]+)%', src or '')
            if m:
                pct = float(m.group(1))
                break
        if pct is None or not (0 < pct <= 100):
            continue
        if not (n.parent and n.parent.parent):
            continue
        # 需要一点"条"的证据：自身或父级有背景色，或类名 hint
        has_bg = bool(first_color(n) or first_color(n.parent))
        if not (has_bg or hint(n.parent.classes, 'bar')
                or hint(n.parent.parent.classes, 'bar')):
            continue
        fills.append((n, pct))

    # 在「数据条组的父容器」层面，把连续的若干 row 合并成一张表
    groups = {}
    for n, pct in fills:
        row = n.parent.parent
        container = row.parent
        if container is None:
            continue
        groups.setdefault(id(container), (container, []))[1].append((row, n, pct))

    for container, entries in groups.values():
        rows = [e[0] for e in entries]
        idxs = [i for i, c in enumerate(container.children) if c in rows]
        if not idxs:
            continue
        table_rows = []
        for row, fill, pct in entries:
            track = fill.parent
            color = first_color(fill) or accent
            slot = first_color(track) or '#eeeeee'
            if 'gradient' in slot.lower():
                slot = '#eeeeee'
            texts = _leaf_texts(row, exclude=(fill, track))
            label = texts[0] if texts else ''
            val = texts[-1] if len(texts) > 1 else ('%g%%' % pct)
            inner = make('section', {
                'width': '%g%%' % pct, 'height': '16px', 'background': color,
                'border-radius': '999px', 'font-size': '0', 'line-height': '0'})
            bar = make('section', {
                'height': '16px', 'background': slot, 'border-radius': '999px',
                'font-size': '0', 'line-height': '0'}, children=[inner])
            table_rows.append([
                make('span', {'font-size': '13px', 'color': '#666666'}, children=[label]),
                bar,
                make('span', {'font-size': '13px', 'color': '#c0392b',
                              'font-weight': '700'}, children=[val]),
            ])
        t = table(table_rows, None, widths=['28%', '', '24%'])
        for tr in t.children:
            for td in tr.children:
                td.attrs['style'] = 'border:0;padding:5px 8px;vertical-align:middle'
        a, b = min(idxs), max(idxs)
        container.children[a:b + 1] = [t]
        t.parent = container


def transform_timelines(root):
    """【时间轴】多个子项带 border-left -> 竖线（圆点在伪元素注入后再补，避免重复）"""
    marked = []
    for n in all_nodes(root):
        kids = children_nodes(n)
        if len(kids) < 2:
            continue
        bl = sum(1 for k in kids
                 if 'border-left' in k.orig or 'border-left' in own_style(k))
        if not (hint(n.classes, 'timeline') or bl >= max(2, len(kids) - 1)):
            continue
        line = None
        for k in kids:
            m = COLOR_RE.search(k.orig.get('border-left', '')
                                or own_style(k).get('border-left', ''))
            if m:
                line = m.group(1)
                break
        line = line or '#e8d5c0'
        n.force.setdefault('margin', '26px 0 10px')
        for k in kids:
            k.force.update({
                'border-left': '2px solid %s' % line,
                'padding': '0 0 18px 16px',
                'margin': '0 0 0 4px',
            })
            k.is_timeline_item = True
        marked.append(n)
    return marked


def grid_columns(node):
    """从 grid-template-columns 解析列宽：支持 33%、repeat(3,33%)、1fr 等分"""
    v = node.orig.get('grid-template-columns') or own_style(node).get('grid-template-columns', '')
    if not v:
        return []
    v = re.sub(r'minmax\([^)]*\)', lambda m: m.group(0).split(',')[-1].strip(' )'), v)
    # 先整体展开 repeat(n, X) -> n 个 X（此前按逗号拆 token 会把 repeat(3 拆碎，repeat 从未生效过）
    v = re.sub(r'repeat\(\s*(\d+)\s*,\s*([^)]+?)\s*\)',
               lambda m: ' '.join([m.group(2)] * int(m.group(1))), v)
    cols, fr_count, used = [], 0, 0.0
    for tok in v.split():
        m = re.match(r'^([\d.]+)%$', tok)
        if m:
            cols.append(float(m.group(1)))
            used += float(m.group(1))
            continue
        if re.match(r'^([\d.]+)?fr$', tok):
            cols.append(None)
            fr_count += 1
            continue
        return []          # px/auto/min-content 等不支持，放弃分列
    if fr_count:
        each = max((100.0 - used) / fr_count, 1.0)
        cols = [c if c is not None else each for c in cols]
    return ['%g%%' % c for c in cols]


def transform_columns(root):
    """【兜底】原 flex/grid 横排 -> 转成 table，避免塌成竖排

    列宽两个来源：① 子项各自的 width:xx%  ② 容器的 grid-template-columns
    """
    for n in all_nodes(root):
        if not was_flex(n):
            continue
        kids = children_nodes(n)
        if len(kids) < 2 or any(k.tagname() == 'table' for k in kids):
            continue
        widths, ok = [], True
        for k in kids:
            m = re.search(r'width\s*:\s*([\d.]+)%',
                          k.orig.get('width', '') or own_style(k).get('width', '') or '')
            if not m:
                ok = False
                break
            widths.append('%g%%' % float(m.group(1)))
        if not ok:
            widths = grid_columns(n)
            if not widths or len(widths) != len(kids):
                continue
        t = table([list(kids)], None, widths=widths)
        for tr in t.children:
            for td in tr.children:
                td.attrs['style'] = 'border:0;padding:6px;vertical-align:top'
        idxs = [i for i, c in enumerate(n.children) if isinstance(c, Node)]
        n.children[min(idxs):max(idxs) + 1] = [t]
        t.parent = n


# 伪元素实体化之后再做装饰补全，天然去重
DOT_CHARS = '●○•◆■▪'
QUOTE_CHARS = '“‘"\'「『'


def has_deco(node, chars):
    """节点（含子树）的可见文本开头是否已经是某个装饰字符。

    伪元素实体化可能已经把 ● / “ 注入进去了，这里必须按整棵子树的文本判断，
    否则会和伪元素注入撞车，出现两个圆点、两个引号。
    """
    t = text_of(node).strip()
    return bool(t) and t[0] in chars


def decorate(root, accent, timelines):
    """补全伪元素丢失的装饰：时间轴圆点、金句卡大引号（已有则跳过）"""
    for n in all_nodes(root):
        if getattr(n, 'is_timeline_item', False) and not has_deco(n, DOT_CHARS):
            dot = make('span', {'display': 'inline-block', 'color': accent,
                                'font-size': '15px', 'margin-right': '4px'},
                       children=['●'])
            n.children.insert(0, dot)
            dot.parent = n
    for n in all_nodes(root):
        if not hint(n.classes, 'quote'):
            continue
        # 去重要看整个金句卡容器：伪元素注入的引号挂在容器上，不是内层 p 上
        if has_deco(n, QUOTE_CHARS):
            continue
        target = None
        for c in children_nodes(n):
            if c.tagname() in ('p', 'section', 'blockquote'):
                target = c
                break
        target = target or n
        deco = make('span', {
            'font-family': 'Georgia,serif', 'font-size': '34px',
            'color': '#c9a227', 'line-height': '1',
            'vertical-align': '-4px', 'margin-right': '2px'},
            children=['“'])
        target.children.insert(0, deco)
        deco.parent = target


def apply_pseudo_content(root, pseudo_rules, variables=None):
    """【通用】把 ::before/::after 的 content:"●"/"“" 实体化成真实可见的 span

    微信不支持伪元素，装饰性字符（引号、圆点、序号、箭头）会整片消失。
    这里把它们变成真节点，样式只取安全的那几个属性。
    """
    for n in all_nodes(root):
        for base, ps, decls in pseudo_rules:
            if not selector_matches(n, base):
                continue
            raw = decls.get('content', '').strip()
            m = re.match(r'^["\'](.*)["\']$', raw, re.S)
            if not m:
                continue
            txt = m.group(1)
            # \201C 这类 CSS 转义还原成字符
            txt = re.sub(r'\\([0-9a-fA-F]{1,6})\s?',
                         lambda mm: chr(int(mm.group(1), 16)), txt)
            if not txt.strip():
                continue
            st = {k: resolve_vars(v, variables or {})
                  for k, v in decls.items()
                  if k != 'content' and k in SAFE_PSEUDO_PROPS}
            # 伪元素常带 position:absolute 定位装饰，微信里直接可见化即可
            st.setdefault('display', 'inline-block')
            # 装饰字符常被写成 60px 这种夸张尺寸，压到合理范围
            m2 = re.match(r'^(\d+(?:\.\d+)?)px$', st.get('font-size', ''))
            if m2 and float(m2.group(1)) > 42:
                st['font-size'] = '34px'
            sp = make('span', st, children=[txt])
            if 'before' in ps:
                n.children.insert(0, sp)
                n.pseudo_filled = True
            else:
                n.children.append(sp)
            sp.parent = n


def luminance(color):
    color = color.strip().lower()
    m = re.match(r'#([0-9a-f]{3})$', color)
    if m:
        r, g, bl = [int(c * 2, 16) for c in m.group(1)]
    else:
        m = re.match(r'#([0-9a-f]{6})', color)
        if m:
            h = m.group(1)
            r, g, bl = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        else:
            m = re.match(r'rgba?\((\d+),\s*(\d+),\s*(\d+)', color)
            if not m:
                return 1.0
            r, g, bl = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return (0.299 * r + 0.587 * g + 0.114 * bl) / 255


def fix_dark_contrast(root):
    """深色容器里的 <strong>/<em> 提亮，避免红字压在深底上看不清"""
    targets = {'strong', 'b', 'em', 'i'}

    def walk(n):
        for c in n.children:
            if isinstance(c, Node):
                walk(c)
                if c.tagname() in targets and 'color' not in c.style:
                    anc = c.parent
                    while anc is not None:
                        bg = anc.style.get('background')
                        if bg:
                            if luminance(bg) < 0.42:
                                c.style['color'] = '#ffd9a5'
                            break
                        anc = anc.parent
    walk(root)


def normalize_tags(node):
    """HTML5/语义标签 -> 微信认识的 section/p/span，并补回浏览器默认样式"""
    for c in node.children:
        if isinstance(c, Node):
            tag = c.tagname()
            if tag in TAG_MAP:
                c.tag = TAG_MAP[tag]
            # 换标签后补默认样式（不覆盖作者显式写的）
            for k, v in TAG_FORCE.get(tag, {}).items():
                c.style.setdefault(k, v)
            normalize_tags(c)


def svg_is_defs_container(node):
    st = node.attrs.get('style', '') or ''
    return (node.attrs.get('aria-hidden') == 'true'
            or re.search(r'width\s*:\s*0', st) is not None
            or (len(children_nodes(node)) == 1
                and children_nodes(node)[0].tagname() == 'defs'))


def next_sibling_caption(node):
    """svg 的下一个兄弟若是 figure-caption 类，取其文本作占位标题"""
    p = node.parent
    if p is None:
        return None
    sibs = children_nodes(p)
    try:
        i = sibs.index(node)
    except ValueError:
        return None
    for s in sibs[i + 1:i + 3]:
        if 'caption' in ' '.join(s.classes).lower() or s.tagname() == 'figcaption':
            t = re.sub(r'\s+', ' ', text_of(s)).strip()
            return t or None
    return None


def transform_svgs(root):
    """微信不支持 SVG：隐形 defs 容器直接删，图示 SVG 换成占位块。

    PNG 文件名约定与 svg2png.py 一致：按文档顺序编号「图1.png、图2.png…」。
    """
    n_fig = 0
    for n in all_nodes(root):
        # 注意：n.children 是 Node 与文本混排，必须按原列表索引替换，
        # 用纯节点快照的枚举索引会错位（曾导致替换错位/吞内容）
        for i, c in enumerate(list(n.children)):
            if not isinstance(c, Node) or c.tagname() != 'svg':
                continue
            if svg_is_defs_container(c):
                n.children[i] = ''
                continue
            n_fig += 1
            cap = next_sibling_caption(c) or ''
            cap_short = cap[:26] + ('…' if len(cap) > 26 else '')
            # 图注常自带「图1：xxx」，避免出现「图1：图1：」
            if re.match(r'^图\s*\d', cap_short):
                title_text = '📐 ' + cap_short
            else:
                title_text = '📐 图%d：%s' % (n_fig, cap_short or '配图')
            box = make('section', {
                'margin': '20px 0', 'padding': '18px 14px',
                'border': '2px dashed #cbd5e1', 'border-radius': '8px',
                'background': '#f8fafc', 'text-align': 'center'})
            box.attrs['_svg_ph'] = '1'
            title = make('strong', {'color': '#334155', 'font-size': '15px'},
                         children=[title_text])
            note = make('section', {
                'margin-top': '8px', 'font-size': '13px', 'color': '#64748b',
                'line-height': '1.6'},
                children=['图片文件「图%d.png」已单独导出，请在素材库上传后，'
                          '删除本虚线框并在原位插入图片' % n_fig])
            box.children = [title, note]
            title.parent = note.parent = box
            n.children[i] = box
            box.parent = n
            # 删除原 svg，避免 to_markdown 吐出残缺文本
            c.children = []
        n.children = [c for c in n.children if c is not None]
    return n_fig


def transform_blocks(root):
    """媒体/表单/不可见元素：微信不支持，分别降级为提示块或直接删除。

    绝不静默丢内容——能救的文本（input 的 value、noscript 里的替代文案）都保留。
    """
    for n in all_nodes(root):
        for i, c in enumerate(list(n.children)):
            if not isinstance(c, Node):
                continue
            tag = c.tagname()
            if tag in DROP_INVISIBLE:
                # script/link/style/meta 等：内容绝不能进正文
                n.children[i] = ''
            elif tag in DROP_MEDIA:
                n.children[i] = notice_block(
                    '此处原为 %s（微信不支持），请手动插入视频/图片或删掉本框' % tag)
            elif tag in DROP_FORM:
                val = c.attrs.get('value', '')
                if tag in ('textarea', 'select') and text_of(c).strip():
                    val = text_of(c).strip()
                n.children[i] = (make('span', {'background': '#f1f3f5',
                                               'padding': '1px 6px',
                                               'border-radius': '4px'},
                                      children=[val]) if val else '')


def transform(root, accent):
    """组件层：全部按特征探测，探测不到就安全降级，绝不吞内容"""
    transform_svgs(root)
    transform_blocks(root)
    transform_pillbars(root)
    transform_bars(root, accent)
    timelines = transform_timelines(root)
    transform_columns(root)
    return timelines


# ---------------------------------------------------------------- Markdown 备份

def to_markdown(node, depth=0):
    parts = []
    for c in node.children:
        if isinstance(c, str):
            if c.strip():
                parts.append(c.strip())
            continue
        tag = c.tagname()
        cls = c.classes
        inner = to_markdown(c, depth + 1)
        if tag == '__root__':
            parts.append(inner)
        elif tag in ('html', 'head', 'body', 'meta', 'style', 'script'):
            parts.append(inner)
        elif tag == 'title':
            continue
        elif tag in ('h1', 'h2', 'h3', 'h4'):
            head = re.sub(r'^(\d{2})(\S)', r'\1 \2', inner)
            parts.append('\n\n' + '#' * int(tag[1]) + ' ' + head + '\n\n')
        elif tag == 'p':
            parts.append(inner + '\n\n')
        elif tag == 'table':
            rows = []
            for tr in children_nodes(c):
                cells = [to_markdown(td, depth + 1).replace('\n', ' ').strip()
                         for td in children_nodes(tr)]
                rows.append(cells)
            if rows:
                head = '| ' + ' | '.join(rows[0]) + ' |'
                sep = '| ' + ' | '.join('---' for _ in rows[0]) + ' |'
                body = '\n'.join('| ' + ' | '.join(r) + ' |' for r in rows[1:])
                parts.append('\n\n' + head + '\n' + sep + ('\n' + body if body else '') + '\n\n')
        elif tag == 'li':
            parts.append(inner.strip() + '\n')
        elif tag in ('ol', 'ul'):
            lines = [l for l in inner.split('\n') if l.strip()]
            if tag == 'ol':
                parts.append('\n\n' + '\n'.join(
                    '%d. %s' % (i + 1, l) for i, l in enumerate(lines)) + '\n\n')
            else:
                parts.append('\n\n' + '\n'.join('- ' + l for l in lines) + '\n\n')
        elif tag == 'br':
            parts.append('\n')
        elif tag in ('strong', 'b'):
            parts.append('**' + inner + '**')
        elif tag in ('em', 'i'):
            parts.append('*' + inner + '*')
        elif tag == 'img':
            parts.append('![](' + c.attrs.get('src', '') + ')\n\n')
        elif tag == 'hr':
            parts.append('\n\n---\n\n')
        elif tag == 'blockquote':
            q = '\n'.join('> ' + l for l in inner.strip().split('\n') if l.strip())
            parts.append('\n\n' + q + '\n\n')
        elif c.attrs.get('_svg_ph') == '1':
            parts.append('\n\n> 📐 ' + inner.replace('\n', ' ').strip() + '\n\n')
        else:
            if tag in ('section', 'div'):
                parts.append(inner + ('\n\n' if inner.strip() and not inner.endswith('\n') else ''))
            else:
                parts.append(inner)
    out = ''.join(parts)
    if node.tagname() == '__root__':
        out = re.sub(r'\n{3,}', '\n\n', out)
        out = re.sub(r'[ \t]+\n', '\n', out)
    return out.strip() + ('\n\n' if node.tagname() in ('p', 'h1', 'h2', 'h3') else '')


# ---------------------------------------------------------------- 序列化

INLINE_TAGS = {'span', 'strong', 'b', 'em', 'i', 'u', 'a', 'code', 'br', 'img', 'sub', 'sup'}

BLOCK_TAGS = {'section', 'div', 'p', 'table', 'thead', 'tbody', 'tr', 'ul', 'ol',
              'blockquote', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
              'html', 'head', 'body', '__root__'}

TAG_MAP = {
    'div': 'section',
    # HTML5 语义块：微信只认 section/p/span 这套，全部降维
    'article': 'section', 'main': 'section', 'header': 'section',
    'footer': 'section', 'nav': 'section', 'aside': 'section',
    'figure': 'section', 'details': 'section', 'summary': 'section',
    'figcaption': 'section', 'address': 'p', 'center': 'section',
    'dl': 'section', 'dt': 'p', 'dd': 'p',
    # 内联语义：微信没默认样式，转 span 并手工补样式（见 TAG_FORCE）
    'mark': 'span', 'kbd': 'span', 'abbr': 'span', 'cite': 'span',
    'q': 'span', 'time': 'span', 'samp': 'span', 'var': 'span',
    'del': 'span', 'ins': 'span', 'big': 'span',
    # 代码块：微信不认 pre 的等宽语义，转 section + pre-wrap
    'pre': 'section',
}

# 换标签后必须补回浏览器默认样式，否则语义丢失
TAG_FORCE = {
    'mark': {'background': '#fff3a3', 'color': '#333'},
    'del': {'text-decoration': 'line-through'},
    'ins': {'text-decoration': 'underline'},
    'code': {'font-family': 'Menlo,Consolas,monospace', 'font-size': '0.92em',
             'background': '#f1f3f5', 'padding': '2px 6px', 'border-radius': '4px'},
    'pre': {'font-family': 'Menlo,Consolas,monospace', 'font-size': '13px',
            'background': '#f6f8fa', 'padding': '14px 16px',
            'border-radius': '8px', 'white-space': 'pre-wrap',
            'word-break': 'break-all', 'line-height': '1.6',
            'margin': '16px 0', 'text-align': 'left'},
    'blockquote': {'border-left': '4px solid #d0d0d0', 'padding': '8px 0 8px 16px',
                   'color': '#666', 'margin': '16px 0'},
    'q': {},
}

# 微信完全不支持的媒体/交互元素 → 换占位提示
DROP_MEDIA = {'iframe', 'video', 'audio', 'object', 'embed', 'canvas',
              'map', 'svg'}
DROP_FORM = {'input', 'select', 'textarea', 'button', 'label',
             'progress', 'meter', 'output', 'form'}
DROP_INVISIBLE = {'noscript', 'template', 'script', 'style', 'link',
                  'meta', 'title', 'head'}


def style_str(style):
    order = ['display', 'margin', 'padding', 'width', 'max-width', 'height', 'line-height',
             'font-size', 'font-weight', 'font-family', 'color', 'background',
             'border', 'border-left', 'border-radius', 'text-align', 'letter-spacing',
             'text-indent', 'box-shadow', 'border-collapse', 'vertical-align', 'opacity']
    items = []
    for k in order:
        if k in style:
            items.append((k, style.pop(k)))
    for k, v in style.items():
        items.append((k, v))
    return ';'.join('%s:%s' % (k, v) for k, v in items if v not in (None, ''))


def serialize(node, indent=0, parent_tag='__root__'):
    if isinstance(node, str):
        if parent_tag in BLOCK_TAGS and node.strip() == '':
            return ''
        return node
    if node.tagname() == '__root__':
        return ''.join(serialize(c, indent) for c in node.children)
    tag = TAG_MAP.get(node.tagname(), node.tagname())
    if tag in ('html', 'head', 'body'):
        return ''.join(serialize(c, indent, tag) for c in node.children)
    if tag in ('meta', 'title', 'link', 'script', 'style'):
        return ''
    attrs = ''
    # img 必须保住 src/alt，否则粘贴后图片位置彻底失忆（也无法提示外链）
    if node.tagname() == 'img':
        for a in ('src', 'alt', 'width', 'height'):
            v = node.attrs.get(a)
            if v:
                attrs += ' %s="%s"' % (a, str(v).replace('"', "'")[:200])
        node.style.setdefault('max-width', '100%')
        node.style.setdefault('height', 'auto')
    style = style_str(dict(node.style))
    if style:
        attrs += ' style="%s"' % style.replace('"', "'")
    void = tag in VOID
    inline = tag in INLINE_TAGS
    pad = '' if inline else '\n' + '  ' * indent
    pad_end = '' if inline else '\n' + '  ' * max(indent - 1, 0)
    if void:
        return '%s<%s%s>' % (pad, tag, attrs)
    kids = ''.join(serialize(c, indent + 1, tag) for c in node.children)
    return '%s<%s%s>%s%s</%s>' % (pad, tag, attrs, kids, pad_end, tag)


def cleanup(node):
    node.attrs.pop('class', None)
    node.attrs.pop('id', None)
    for c in node.children:
        if isinstance(c, Node):
            cleanup(c)


# ---------------------------------------------------------------- 体检报告

def report(content, verbose=True):
    plain = re.sub(r'<[^>]+>', '', content)
    plain = re.sub(r'\s+', ' ', plain).strip()
    imgs = re.findall(r'<img[^>]*>', content)
    remote = [i for i in imgs if re.search(r'src\s*=\s*["\']https?:', i)]
    stats = [
        ('正文字符', len(plain)),
        ('残留 class', len(re.findall(r'\sclass=', content))),
        ('残留 flex/grid', len(re.findall(r'(?:^|[";])\s*display:\s*(flex|inline-flex|grid|inline-grid)', content))),
        ('残留 position', len(re.findall(r'(?:^|;)\s*position\s*:', content))),
        # 只检测真实样式里的渐变（style 属性值），避免 <code>linear-gradient</code> 这类正文文本误报
        ('残留 gradient', sum(len(re.findall(r'(linear|radial)-gradient', s))
                              for s in re.findall(r'style="([^"]*)"', content))),
        ('section 块', len(re.findall(r'<section', content))),
        ('段落 p', len(re.findall(r'<p[ >]', content))),
        ('表格 table', len(re.findall(r'<table', content))),
        ('小标题', len(re.findall(r'<h[1-4][ >]', content))),
        ('图片', '%d 张（外链 %d 张须上传素材库）' % (len(imgs), len(remote)) if imgs else '0 张'),
        ('SVG 占位', len(re.findall(r'此处插入图片|上传后，\s*删除本虚线框', content))),
    ]
    if verbose:
        print('\n'.join('  %-14s %s' % (k, v) for k, v in stats))
    return dict(stats)


# ---------------------------------------------------------------- 主流程

def convert(html_text, orig_title='', accent=None, do_report=False):
    builder = DomBuilder()
    builder.feed(html_text)
    root = builder.root

    rules, variables, pseudo_rules = {}, {}, []
    base = {}
    for blk in builder.style_blocks:
        r, v, p, b = parse_css(blk)
        variables.update(v)
        pseudo_rules.extend(p)
        base.update(b)
        for sel, decls in r:
            rules.setdefault(sel, {}).update(decls)
    rules_list = list(rules.items())

    precompute_orig(root, rules_list, variables)
    accent = accent or guess_accent(root, variables)

    timelines = transform(root, accent)
    inline_styles(root, rules_list, variables)
    apply_pseudo_content(root, pseudo_rules, variables)
    decorate(root, accent, timelines)
    fix_dark_contrast(root)
    normalize_tags(root)
    cleanup(root)

    body = ''.join(serialize(c, 1) for c in root.children)

    # 全局样式（html/body/* 上定义的）继承到外层容器：字体、颜色、行高、背景等
    base = {k: resolve_vars(v, variables) for k, v in base.items()}
    sanitize_props(base)
    for sel, decls in rules_list:
        if sel.strip() == 'body':
            for k, v in decls.items():
                base.setdefault(k, resolve_vars(v, variables))
            break
    wrapper_style = {k: v for k, v in base.items() if k in INHERITABLE_PROPS}
    wrapper_style.setdefault('font-family',
                             '-apple-system,BlinkMacSystemFont,"PingFang SC",'
                             '"Microsoft YaHei",sans-serif')
    wrapper_style.setdefault('font-size', '16px')
    wrapper_style.setdefault('line-height', '1.75')
    wrapper_style.setdefault('color', '#333333')
    wrapper_style.setdefault('background', '#ffffff')
    wrapper_style.setdefault('padding', '1px 0 20px')
    wrapper_style['max-width'] = '680px'      # 微信正文区实际渲染宽度
    body = '<section style="%s">%s\n</section>' % (style_str(wrapper_style), body)

    if do_report:
        report(body)
    return body


USAGE = __doc__


def main():
    argv = sys.argv[1:]
    flags = {a for a in argv if a.startswith('--')}
    args = [a for a in argv if not a.startswith('--')]
    if len(args) < 2:
        print(USAGE)
        sys.exit(1)
    src, dst = args[0], args[1]

    accent = None
    for a in argv:
        if a.startswith('--accent='):
            accent = a.split('=', 1)[1].strip()
    for a in argv:
        if a.startswith('--map='):
            for item in a.split('=', 1)[1].split(','):
                if ':' in item:
                    k, v = item.split(':', 1)
                elif '.' in item and item.index('.') == 0:
                    continue
                else:
                    continue
                k = k.strip().lower()
                if k in CLASS_HINTS:
                    CLASS_HINTS[k] = tuple(list(CLASS_HINTS[k]) + [v.strip()])

    with open(src, 'r', encoding='utf-8') as f:
        raw = f.read()
    m = re.search(r'<title>(.*?)</title>', raw, re.S)
    title = m.group(1).strip() if m else '文章'
    content = convert(raw, title, accent=accent, do_report='--check' in flags)

    if '--fragment' in flags:
        with open(dst, 'w', encoding='utf-8') as f:
            f.write(content)
        print('✅ 正文片段：%s' % dst)
    else:
        out = PAGE_WRAPPER.format(content=content, title=html_mod.escape(title))
        with open(dst, 'w', encoding='utf-8') as f:
            f.write(out)
        print('✅ 微信粘贴版：%s（正文 %d 字符）' % (dst, len(re.sub(r'<[^>]+>', '', content))))

    if '--md' in flags:
        b = DomBuilder()
        b.feed(raw)
        md = re.sub(r'\n{3,}', '\n\n', to_markdown(b.root))
        md_file = dst.rsplit('.', 1)[0] + '.md'
        with open(md_file, 'w', encoding='utf-8') as f:
            f.write('# ' + title + '\n\n' + md)
        print('✅ Markdown 备份：%s' % md_file)


if __name__ == '__main__':
    main()
