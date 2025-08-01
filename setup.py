import sys

vi = sys.version_info
if vi < (3, 8):
    raise RuntimeError('winloop requires Python 3.8 or greater')

# Winloop comment: winloop now supports both Windows and non-Windows.
# Below uvloop's setup.py is merged with winloop's previous setup.py.


import os
import os.path
import pathlib
import platform
import re
import shutil
import subprocess

from setuptools import setup, Extension
from setuptools.command.build_ext import build_ext
from setuptools.command.sdist import sdist

# Using a newer version of cython since versions are no longer a threat. 
# Cython Decided to keep DEF Statements. 
CYTHON_DEPENDENCY = 'Cython>=3.1.2'
MACHINE = platform.machine()
MODULES_CFLAGS = [os.getenv('UVLOOP_OPT_CFLAGS', '-O2')]
_ROOT = pathlib.Path(__file__).parent
LIBUV_DIR = str(_ROOT / 'vendor' / 'libuv')
LIBUV_BUILD_DIR = str(_ROOT / 'build' / 'libuv-{}'.format(MACHINE))


def _libuv_build_env():
    env = os.environ.copy()

    cur_cflags = env.get('CFLAGS', '')
    if not re.search(r'-O\d', cur_cflags):
        cur_cflags += ' -O2'

    env['CFLAGS'] = (cur_cflags + ' -fPIC ' + env.get('ARCHFLAGS', ''))

    return env


def _libuv_autogen(env):
    if os.path.exists(os.path.join(LIBUV_DIR, 'configure')):
        # No need to use autogen, the configure script is there.
        return

    if not os.path.exists(os.path.join(LIBUV_DIR, 'autogen.sh')):
        raise RuntimeError(
            'the libuv submodule has not been checked out; '
            'try running "git submodule init; git submodule update"')

    subprocess.run(
        ['/bin/sh', 'autogen.sh'], cwd=LIBUV_DIR, env=env, check=True)


class uvloop_sdist(sdist):
    def run(self):
        if sys.platform != 'win32':
            # Make sure sdist archive contains configure
            # to avoid the dependency on autotools.
            _libuv_autogen(_libuv_build_env())
        super().run()


class uvloop_build_ext(build_ext):
    user_options = build_ext.user_options + [
        ('cython-always', None,
            'run cythonize() even if .c files are present'),
        ('cython-annotate', None,
            'Produce a colorized HTML version of the Cython source.'),
        ('cython-directives=', None,
            'Cythion compiler directives'),
        ('use-system-libuv', None,
            'Use the system provided libuv, instead of the bundled one'),
    ]

    boolean_options = build_ext.boolean_options + [
        'cython-always',
        'cython-annotate',
        'use-system-libuv',
    ]

    def initialize_options(self):
        super().initialize_options()
        self.use_system_libuv = False
        self.cython_always = False
        self.cython_annotate = False
        self.cython_directives = None

    def finalize_options(self):
        need_cythonize = self.cython_always
        cfiles = {}

        for extension in self.distribution.ext_modules:
            for i, sfile in enumerate(extension.sources):
                if sfile.endswith('.pyx'):
                    prefix, ext = os.path.splitext(sfile)
                    cfile = prefix + '.c'

                    if os.path.exists(cfile) and not self.cython_always:
                        extension.sources[i] = cfile
                    else:
                        if os.path.exists(cfile):
                            cfiles[cfile] = os.path.getmtime(cfile)
                        else:
                            cfiles[cfile] = 0
                        need_cythonize = True

        if need_cythonize:
            import pkg_resources

            # Double check Cython presence in case setup_requires
            # didn't go into effect (most likely because someone
            # imported Cython before setup_requires injected the
            # correct egg into sys.path.
            try:
                import Cython
            except ImportError:
                raise RuntimeError(
                    'please install {} to compile uvloop from source'.format(
                        CYTHON_DEPENDENCY))

            cython_dep = pkg_resources.Requirement.parse(CYTHON_DEPENDENCY)
            if Cython.__version__ not in cython_dep:
                raise RuntimeError(
                    'uvloop requires {}, got Cython=={}'.format(
                        CYTHON_DEPENDENCY, Cython.__version__
                    ))

            from Cython.Build import cythonize

            directives = {}
            if self.cython_directives:
                for directive in self.cython_directives.split(','):
                    k, _, v = directive.partition('=')
                    if v.lower() == 'false':
                        v = False
                    if v.lower() == 'true':
                        v = True

                    directives[k] = v
                self.cython_directives = directives

            self.distribution.ext_modules[:] = cythonize(
                self.distribution.ext_modules,
                compiler_directives=directives,
                annotate=self.cython_annotate,
                compile_time_env=dict(DEFAULT_FREELIST_SIZE=250),
                emit_linenums=self.debug)

        super().finalize_options()

    def build_libuv(self):
        env = _libuv_build_env()

        # Make sure configure and friends are present in case
        # we are building from a git checkout.
        _libuv_autogen(env)

        # Copy the libuv tree to build/ so that its build
        # products don't pollute sdist accidentally.
        if os.path.exists(LIBUV_BUILD_DIR):
            shutil.rmtree(LIBUV_BUILD_DIR)
        shutil.copytree(LIBUV_DIR, LIBUV_BUILD_DIR)

        # Sometimes pip fails to preserve the timestamps correctly,
        # in which case, make will try to run autotools again.
        subprocess.run(
            ['touch', 'configure.ac', 'aclocal.m4', 'configure',
             'Makefile.am', 'Makefile.in'],
            cwd=LIBUV_BUILD_DIR, env=env, check=True)

        if 'LIBUV_CONFIGURE_HOST' in env:
            cmd = ['./configure', '--host=' + env['LIBUV_CONFIGURE_HOST']]
        else:
            cmd = ['./configure']
        subprocess.run(
            cmd,
            cwd=LIBUV_BUILD_DIR, env=env, check=True)

        try:
            njobs = len(os.sched_getaffinity(0))
        except AttributeError:
            njobs = os.cpu_count()
        j_flag = '-j{}'.format(njobs or 1)
        c_flag = "CFLAGS={}".format(env['CFLAGS'])
        subprocess.run(
            ['make', j_flag, c_flag],
            cwd=LIBUV_BUILD_DIR, env=env, check=True)

    def build_extensions(self):
        if sys.platform == 'win32':
            path = pathlib.Path("vendor", "libuv", "src")
            c_files = [p.as_posix() for p in path.iterdir() if p.suffix == '.c']
            c_files += [p.as_posix() for p in (path/'win').iterdir() if p.suffix == '.c']
            self.extensions[-1].sources += c_files
            super().build_extensions()
            return

        if self.use_system_libuv:
            self.compiler.add_library('uv')

            if sys.platform == 'darwin' and \
                    os.path.exists('/opt/local/include'):
                # Support macports on Mac OS X.
                self.compiler.add_include_dir('/opt/local/include')
        else:
            libuv_lib = os.path.join(LIBUV_BUILD_DIR, '.libs', 'libuv.a')
            if not os.path.exists(libuv_lib):
                self.build_libuv()
            if not os.path.exists(libuv_lib):
                raise RuntimeError('failed to build libuv')

            self.extensions[-1].extra_objects.extend([libuv_lib])
            self.compiler.add_include_dir(os.path.join(LIBUV_DIR, 'include'))

        if sys.platform.startswith('linux'):
            self.compiler.add_library('rt')
        elif sys.platform.startswith(('freebsd', 'dragonfly')):
            self.compiler.add_library('kvm')
        elif sys.platform.startswith('sunos'):
            self.compiler.add_library('kstat')

        self.compiler.add_library('pthread')

        super().build_extensions()


with open(str(_ROOT / 'winloop' / '_version.py')) as f:
    for line in f:
        if line.startswith('__version__ ='):
            _, _, version = line.partition('=')
            VERSION = version.strip(" \n'\"")
            break
    else:
        raise RuntimeError(
            'unable to read the version from winloop/_version.py')


if sys.platform == 'win32':
    from Cython.Build import cythonize
    from Cython.Compiler.Main import default_options

    default_options['compile_time_env'] = dict(DEFAULT_FREELIST_SIZE=250)
    ext = cythonize([
        Extension(
            "winloop.loop",
            sources=[
                "winloop/loop.pyx"
            ],
            include_dirs=[
                "vendor/libuv/src",
                "vendor/libuv/src/win",
                "vendor/libuv/include"
            ],
            extra_link_args=[  # subset of libuv Windows libraries
                "Shell32.lib",
                "Ws2_32.lib",
                "Advapi32.lib",
                "iphlpapi.lib",
                "Userenv.lib",
                "User32.lib",
                "Dbghelp.lib",
                "Ole32.lib"
            ],
            define_macros=[
                ("WIN32_LEAN_AND_MEAN", 1),
                ("_WIN32_WINNT", "0x0602")
            ],
        ),
    ])
else:
    ext = [
            Extension(
                "winloop.loop",
                sources=[
                    "winloop/loop.pyx",
                ],
                extra_compile_args=MODULES_CFLAGS
            ),
        ]

setup_requires = []

if not (_ROOT / 'winloop' / 'loop.c').exists() or '--cython-always' in sys.argv:
    # No Cython output, require Cython to build.
    setup_requires.append(CYTHON_DEPENDENCY)


setup(
    version=VERSION,
    cmdclass={
        'sdist': uvloop_sdist,
        'build_ext': uvloop_build_ext
    },
    ext_modules=ext,
    setup_requires=setup_requires
)
