# -*- coding: utf-8 -*-
# This file is part of the markdown-katex project
# https://github.com/mbarkhau/markdown-katex
#
# Copyright (c) 2019-2021 Manuel Barkhau (mbarkhau@gmail.com) - MIT License
# SPDX-License-Identifier: MIT

# NOTE (mb 2019-05-16): This module is substantially shared with the
#   markdown-svgbob package and meaningful changes should be
#   replicated there also.

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals
import os
import re
import time
import signal
import typing as typ
import hashlib
import platform
import tempfile
import subprocess as sp
try:
    import builtins
except ImportError:
    import __builtin__ as builtins
import pathlib2 as pl
str = getattr(builtins, 'unicode', str)
SIG_NAME_BY_NUM = {k: v for v, k in sorted(signal.__dict__.items(), reverse
    =True) if v.startswith('SIG') and not v.startswith('SIG_')}
assert SIG_NAME_BY_NUM[15] == 'SIGTERM'
TMP_DIR = pl.Path(tempfile.gettempdir()) / 'mdkatex'
LIBDIR = pl.Path(__file__).parent
PKG_BIN_DIR = LIBDIR / 'bin'
FALLBACK_BIN_DIR = pl.Path('/') / 'usr' / 'local' / 'bin'
FALLBACK_BIN_DIR = FALLBACK_BIN_DIR.expanduser()
CMD_NAME = 'katex'
OSNAME = platform.system()
MACHINE = platform.machine()
KATEX_INPUT_ENCODING = 'UTF-8'
KATEX_OUTPUT_ENCODING = 'UTF-8'
TMP_LOCAL_CMD_CACHE = TMP_DIR / 'local_katex_cmd.txt'


def _get_env_paths():
    env_path = os.environ.get('PATH')
    if env_path:
        path_strs = env_path.split(os.pathsep)
        for path_str in path_strs:
            yield pl.Path(path_str)
    if env_path is None or str(FALLBACK_BIN_DIR) not in env_path:
        yield FALLBACK_BIN_DIR


def _get_local_bin_candidates():
    if OSNAME == 'Windows':
        return ['{0}.cmd'.format(CMD_NAME), '{0}.exe'.format(CMD_NAME),
            'npx.cmd --no-install {0}'.format(CMD_NAME),
            'npx.exe --no-install {0}'.format(CMD_NAME), '{0}.ps1'.format(
            CMD_NAME), 'npx.ps1 --no-install {0}'.format(CMD_NAME)]
    else:
        return [CMD_NAME, 'npx --no-install {0}'.format(CMD_NAME)]


def _get_usr_parts():
    if TMP_LOCAL_CMD_CACHE.exists():
        with TMP_LOCAL_CMD_CACHE.open(mode='r', encoding='utf-8') as fobj:
            local_cmd = typ.cast(str, fobj.read())
        local_cmd_parts = local_cmd.split('\n')
        if pl.Path(local_cmd_parts[0]).exists():
            return local_cmd_parts
    for path in _get_env_paths():
        for local_cmd in _get_local_bin_candidates():
            local_cmd_parts = local_cmd.split()
            bin_name = local_cmd_parts[0]
            local_bin = path / bin_name
            if not local_bin.is_file():
                continue
            local_cmd_parts[0] = str(local_bin)
            try:
                output_data = sp.check_output(local_cmd_parts + [
                    '--version'], stderr=sp.STDOUT)
                output_text = output_data.decode('utf-8')
                if re.match('\\d+\\.\\d+\\.\\d+', output_text.strip()) is None:
                    continue
            except sp.CalledProcessError:
                continue
            except OSError:
                continue
            TMP_DIR.mkdir(parents=True, exist_ok=True)
            with TMP_LOCAL_CMD_CACHE.open(mode='w', encoding='utf-8') as fobj:
                fobj.write('\n'.join(local_cmd_parts))
            return local_cmd_parts
    return None


def _get_pkg_bin_path(osname=OSNAME, machine=MACHINE):
    if machine == 'AMD64':
        machine = 'x86_64'
    glob_expr = '*_{0}-{1}*'.format(machine, osname)
    bin_files = list(PKG_BIN_DIR.glob(glob_expr))
    if bin_files:
        return max(bin_files)
    err_msg = (
        "Platform not supported, katex binary not found. Install manually using 'npm install katex'."
        )
    raise NotImplementedError(err_msg)


def get_bin_cmd():
    usr_bin_cmd = _get_usr_parts()
    if usr_bin_cmd is None:
        return [str(_get_pkg_bin_path())]
    else:
        return usr_bin_cmd


def _iter_output_lines(buf):
    while True:
        output = buf.readline()
        if output:
            yield output
        else:
            return


def read_output(buf):
    assert buf is not None
    return b''.join(_iter_output_lines(buf)).decode('utf-8')


ArgValue = typ.Union[str, int, float, bool]
Options = typ.Dict[str, ArgValue]


class KatexError(Exception):
    pass


def _iter_cmd_parts(options=None):
    for cmd_part in get_bin_cmd():
        yield cmd_part
    if options:
        for option_name, option_value in options.items():
            if option_name.startswith('--'):
                arg_name = option_name
            else:
                arg_name = '--' + option_name
            if option_value is True:
                yield arg_name
            elif option_value is False:
                continue
            else:
                arg_value = str(option_value)
                yield arg_name
                yield arg_value


def _cmd_digest(tex, cmd_parts):
    hasher = hashlib.sha256(tex.encode('utf-8'))
    for cmd_part in cmd_parts:
        hasher.update(cmd_part.encode('utf-8'))
    return hasher.hexdigest()


def _write_tex2html(cmd_parts, tex, tmp_output_file):
    tmp_input_file = TMP_DIR / tmp_output_file.name.replace('.html', '.tex')
    input_data = tex.encode(KATEX_INPUT_ENCODING)
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    with tmp_input_file.open(mode='wb') as fobj:
        fobj.write(input_data)
    cmd_parts.extend(['--input', str(tmp_input_file), '--output', str(
        tmp_output_file)])
    proc = None
    try:
        proc = sp.Popen(cmd_parts, stdout=sp.PIPE, stderr=sp.PIPE)
        ret_code = proc.wait()
        if ret_code < 0:
            signame = SIG_NAME_BY_NUM[abs(ret_code)]
            err_msg = "Error processing '{0}': ".format(tex
                ) + 'katex_cli process ended with ' + 'code {0} ({1})'.format(
                ret_code, signame)
            raise KatexError(err_msg)
        elif ret_code > 0:
            stdout = read_output(proc.stdout)
            errout = read_output(proc.stderr)
            output = (stdout + '\n' + errout).strip()
            err_msg = "Error processing '{0}': {1}".format(tex, output)
            raise KatexError(err_msg)
    finally:
        if proc is not None:
            if proc.stdout is not None:
                proc.stdout.close()
            if proc.stderr is not None:
                proc.stderr.close()
    tmp_input_file.unlink()


def tex2html(tex, options=None):
    cmd_parts = list(_iter_cmd_parts(options))
    digest = _cmd_digest(tex, cmd_parts)
    tmp_filename = digest + '.html'
    tmp_output_file = TMP_DIR / tmp_filename
    try:
        if tmp_output_file.exists():
            tmp_output_file.touch()
        else:
            _write_tex2html(cmd_parts, tex, tmp_output_file)
        with tmp_output_file.open(mode='r', encoding=KATEX_OUTPUT_ENCODING
            ) as fobj:
            result = typ.cast(str, fobj.read())
            return result.strip()
    finally:
        _cleanup_tmp_dir()


def _cleanup_tmp_dir():
    min_mtime = time.time() - 24 * 60 * 60
    for fpath in TMP_DIR.iterdir():
        if fpath.is_file():
            mtime = fpath.stat().st_mtime
            if mtime < min_mtime:
                fpath.unlink()


DEFAULT_HELP_TEXT = """
Options:
  -V, --version              output the version number
  -d, --display-mode         Render math in display...
  --leqno                    Render display math in...
  --fleqn                    Render display math fl...
  -t, --no-throw-on-error    Render errors (in the ...
  -c, --error-color <color>  A color string given i...
  -b, --color-is-text-color  Makes \\color behave li...
  -S, --strict               Turn on strict / LaTeX...
  -s, --max-size <n>         If non-zero, all user-...
  -e, --max-expand <n>       Limit the number of ma...
  -m, --macro <def>          Define custom macro of...
  -f, --macro-file <path>    Read macro definitions...
  -i, --input <path>         Read LaTeX input from ...
  -o, --output <path>        Write html output to t...
  -h, --help                 output usage information
"""
DEFAULT_HELP_TEXT = DEFAULT_HELP_TEXT.replace('\n', ' ').replace('NL', '\n')


def _get_cmd_help_text():
    bin_parts = get_bin_cmd()
    cmd_parts = bin_parts + ['--help']
    proc = None
    try:
        proc = sp.Popen(cmd_parts, stdout=sp.PIPE)
        help_text = read_output(proc.stdout)
    finally:
        if proc is not None and proc.stdout is not None:
            proc.stdout.close()
    return help_text


OptionsHelp = typ.Dict[str, str]
OPTION_PATTERN = """
    --
    (?P<name>[a-z\\-]+)
    \\s+(?:<[a-z\\-]+>)?
    \\s+
    (?P<text>[^\\n]*[ \\s\\w(){},:;.'\\\\/\\[\\] ]*)
"""
OPTION_REGEX = re.compile(OPTION_PATTERN, flags=re.VERBOSE | re.DOTALL | re
    .MULTILINE)


def _parse_options_help_text(help_text):
    options = {}
    options_text = help_text.split('Options:', 1)[-1]
    for match in OPTION_REGEX.finditer(options_text):
        name = match.group('name')
        text = match.group('text')
        text = ' '.join(line.strip() for line in text.splitlines())
        options[name] = text.strip()
    options.pop('version', None)
    options.pop('help', None)
    options.pop('input', None)
    options.pop('output', None)
    options.pop('display-mode', None)
    return options


_PARSED_OPTIONS = {}


def parse_options():
    if _PARSED_OPTIONS:
        return _PARSED_OPTIONS
    options = _parse_options_help_text(DEFAULT_HELP_TEXT)
    try:
        help_text = _get_cmd_help_text()
        cmd_options = _parse_options_help_text(help_text)
        options.update(cmd_options)
    except NotImplementedError:
        pass
    _PARSED_OPTIONS.update(options)
    return options
