# -*- coding: utf-8 -*-
# This file is part of the markdown-katex project
# https://github.com/mbarkhau/markdown-katex
#
# Copyright (c) 2019-2021 Manuel Barkhau (mbarkhau@gmail.com) - MIT License
# SPDX-License-Identifier: MIT
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals
import re
import json
import base64
import typing as typ
import hashlib
import logging
from markdown.extensions import Extension
from markdown.preprocessors import Preprocessor
from markdown.postprocessors import Postprocessor
from . import wrapper
try:
    import builtins
except ImportError:
    import __builtin__ as builtins
from .html import KATEX_STYLES
str = getattr(builtins, 'unicode', str)
logger = logging.getLogger(__name__)
SVG_ELEM_RE = re.compile('<svg.*?</svg>', flags=re.MULTILINE | re.DOTALL)
SVG_XMLNS = ('xmlns="http://www.w3.org/2000/svg" ' +
    'xmlns:xlink="http://www.w3.org/1999/xlink" ')
B64IMG_TMPL = '<img src="data:image/svg+xml;base64,{img_text}"/>'
FENCE_RE = re.compile(r'^(\s*)(\${2,})')
BLOCK_START_RE = re.compile(r'^(\s*)(\${2,})')
BLOCK_CLEAN_RE = re.compile('^(\s*)(\${2,})\s(.*)\s(\${2,})$', flags=re.
    DOTALL)


def _clean_block_text(block_text):
    block_match = BLOCK_CLEAN_RE.match(block_text)
    if block_match:
        return block_match.group(3)
    else:
        return block_text


def make_marker_id(text):
    data = text.encode('utf-8')
    return hashlib.md5(data).hexdigest()


def svg2img(html):
    """Converts inline svg elements to images.

    This is done as a workaround for #75 of WeasyPrint
    https://github.com/Kozea/WeasyPrint/issues/75
    """
    while True:
        match = SVG_ELEM_RE.search(html)
        if match:
            svg_text = match.group(0)
            if 'xmlns' not in svg_text:
                svg_text = svg_text.replace('<svg ', '<svg ' + SVG_XMLNS)
            svg_data = svg_text.encode('utf-8')
            img_b64_data = base64.standard_b64encode(svg_data)
            img_b64_text = img_b64_data.decode('utf-8')
            img_b64_tag = B64IMG_TMPL.format(img_text=img_b64_text)
            start, end = match.span()
            html = html[:start] + img_b64_tag + html[end:]
        else:
            break
    return html


def tex2html(tex, options=None):
    if options:
        no_inline_svg = options.get('no_inline_svg', False)
    else:
        no_inline_svg = False
    if options:
        options.pop('no_inline_svg', None)
        options.pop('insert_fonts_css', None)
    result = wrapper.tex2html(tex, options)
    if no_inline_svg:
        result = svg2img(result)
    return result


def md_block2html(block_text, default_options=None):
    options = {'display-mode': True}
    if default_options:
        options.update(default_options)
    block_text = _clean_block_text(block_text)
    # header, rest = block_text.split('\n', 1)
    # if '{' in header and '}' in header:
    #     options.update(json.loads(header))
    #     block_text = rest
    return tex2html(block_text, options)


def _clean_inline_text(inline_text):
    if inline_text.startswith("$"):
        inline_text = inline_text[len('$'):]
    if inline_text.endswith('$'):
        inline_text = inline_text[:-len('$')]
    return inline_text


def md_inline2html(inline_text, default_options=None):
    options = default_options.copy() if default_options else {}
    inline_text = _clean_inline_text(inline_text)
    return tex2html(inline_text, options)


INLINE_DELIM_RE = re.compile(r'(\$[^\n\r\t\$]+?\$)')
InlineCodeItem = typ.NamedTuple('InlineCodeItem', [('inline_text', str), (
    'start', int), ('end', int)])


def iter_inline_katex(line: str):
    pos = 0
    while True:
        inline_match_start = INLINE_DELIM_RE.search(line, pos)
        if inline_match_start is None:
            break

        start = inline_match_start.start()
        end = inline_match_start.end()

        pos = end

        # check if we are not inside `__` brackets
        if line.count("`", 0, start) % 2 == 1:
            continue

        inline_text = line[start:end]

        yield InlineCodeItem(inline_text, start, end)


class KatexExtension(Extension):

    def __init__(self, **kwargs):
        self.config = {'no_inline_svg': ['',
            'Replace inline <svg> with <img> tags.'], 'insert_fonts_css': [
            '', 'Insert font loading stylesheet.']}
        for name, options_text in wrapper.parse_options().items():
            self.config[name] = ['', options_text]
        self.options = {}
        for name in self.config:
            val_configured = self.getConfig(name, '')
            val = kwargs.get(name, val_configured)
            if val != '':
                self.options[name] = val
        self.math_html = {}
        super(KatexExtension, self).__init__(**kwargs)

    def reset(self):
        self.math_html.clear()

    def extendMarkdown(self, md):
        preproc = KatexPreprocessor(md, self)
        md.preprocessors.register(preproc, name='katex_fenced_code_block',
            priority=50)
        postproc = KatexPostprocessor(md, self)
        md.postprocessors.register(postproc, name='katex_fenced_code_block',
            priority=0)
        md.registerExtension(self)


class KatexPreprocessor(Preprocessor):

    def __init__(self, md, ext):
        super(KatexPreprocessor, self).__init__(md)
        self.ext = ext

    def _make_tag_for_block(self, block_lines):
        indent_len = len(block_lines[0]) - len(block_lines[0].lstrip())
        indent_text = block_lines[0][:indent_len]
        block_text = '\n'.join(line[indent_len:] for line in block_lines
            ).rstrip()
        marker_id = make_marker_id('block' + block_text)
        marker_tag = 'tmp_block_md_katex_{0}'.format(marker_id)
        math_html = md_block2html(block_text, self.ext.options)
        self.ext.math_html[marker_tag] = '<p>{0}</p>'.format(math_html)
        return indent_text + marker_tag

    def _make_tag_for_inline(self, inline_text):
        marker_id = make_marker_id('inline' + inline_text)
        marker_tag = 'tmp_inline_md_katex_{0}'.format(marker_id)
        math_html = md_inline2html(inline_text, self.ext.options)
        self.ext.math_html[marker_tag] = math_html
        return marker_tag

    def _iter_out_lines(self, lines):
        is_in_math_fence = False
        is_in_fence = False
        expected_close_fence = '$$'
        block_lines = []
        for line in lines:
            if is_in_fence:
                yield line
                is_ending_fence = line.rstrip() == expected_close_fence
                if is_ending_fence:
                    is_in_fence = False
            elif is_in_math_fence:
                block_lines.append(line)
                is_ending_fence = line.rstrip() == expected_close_fence
                if is_ending_fence:
                    is_in_math_fence = False
                    marker_tag = self._make_tag_for_block(block_lines)
                    del block_lines[:]
                    yield marker_tag
            else:
                math_fence_match = BLOCK_START_RE.match(line)
                fence_match = FENCE_RE.match(line)
                if math_fence_match:
                    is_in_math_fence = True
                    prefix = math_fence_match.group(1)
                    expected_close_fence = prefix + math_fence_match.group(2)
                    block_lines.append(line)
                elif fence_match:
                    is_in_fence = True
                    prefix = fence_match.group(1)
                    expected_close_fence = prefix + fence_match.group(2)
                    yield line
                else:
                    inline_codes = list(iter_inline_katex(line))
                    for code in reversed(inline_codes):
                        marker_tag = self._make_tag_for_inline(code.inline_text
                                                               )
                        line = line[:code.start] + marker_tag + line[code.end:]
                    yield line
        if block_lines:
            for line in block_lines:
                yield line

    def run(self, lines):
        return list(self._iter_out_lines(lines))


class KatexPostprocessor(Postprocessor):

    def __init__(self, md, ext):
        super(KatexPostprocessor, self).__init__(md)
        self.ext = ext

    def run(self, text):
        if any(marker in text for marker in self.ext.math_html):
            if self.ext.options:
                insert_fonts_css = self.ext.options.get('insert_fonts_css',
                    True)
            else:
                insert_fonts_css = True
            if insert_fonts_css and KATEX_STYLES not in text:
                text = KATEX_STYLES + text
            for marker, html in self.ext.math_html.items():
                is_block = marker.startswith('tmp_block_md_katex_')
                is_inline = marker.startswith('tmp_inline_md_katex_')
                assert is_block or is_inline
                if marker in text:
                    if is_block:
                        wrapped_marker = '<p>' + marker + '</p>'
                    else:
                        wrapped_marker = marker
                    while marker in text:
                        if wrapped_marker in text:
                            text = text.replace(wrapped_marker, html)
                        else:
                            text = text.replace(marker, html)
                else:
                    logger.warning("KatexPostprocessor couldn't find: {0}".
                        format(marker))
        return text
