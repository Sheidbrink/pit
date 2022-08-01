#! /usr/bin/env python3
import argparse
import configparser
import difflib
import hashlib
import io
import logging
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

class Pit():
    def __init__(self, root):
        root = Path(root).resolve()
        if root.name != '.pit':
            root = root / '.pit'
        self.root = root
        self.config_fn = root / 'config'

        self._config = None
        self._index = None

    @property
    def config(self):
        if self._config is None:
            self._config =  parse_config(self.config_fn)
        return self._config

    @property
    def object_store(self):
        return Path(self.config['core']['url'])

    @property
    def index_fn(self):
        return self.object_store.parent / 'index'

    @property
    def index(self):
        if self._index is None:
            if ':' in str(self.index_fn): # is remote
                subprocess.run(f'scp {self.index_fn} {self.root}/index', shell=True, check=True)
            with open(self.index_fn, 'r') as fd:
                self._index = fd.readlines()
        return self._index

    def add_to_index(self, entry):
        if ':' in str(self.index_fn):
            host, path = str(self.index_fn).split(':')
            subprocess.run(f'ssh {host} "echo {entry} >> {path}"', shell=True, check=True)
        else:
            with open(self.index_fn, 'a') as fd:
                fd.write(f'{entry}\n')
        if self._index is not None:
            self._index.append(entry)

    def exists(self):
        return self.config_fn.exists()

    def verify_file(self, fn):
        if self.root in fn.parents:
            log.warning(f'Refusing to save file in pit')
            return False
        if fn.is_symlink():
            log.warning(f'Refusing to backup symlink {fn}')
            return False
        if not self.root.parent in fn.resolve().parents:
            log.warning(f'Refusing to add file outside of pit root subdirectory')
            return False
        return True

def main(args):
    config = parse_config()
    match args.command:
        case 'init':
            init(Path.cwd())
        case 'clone':
            clone(args.url, Path.cwd())
        case 'add':
            add(Pit(get_root_pit()), args.path)
        case 'checkout':
            checkout(Pit(get_root_pit()), args.filename)
        case 'dep':
            print(is_newer(args.path, args.dep))
        case 'diff':
            print(diff(args.path, args.cmp, use_shell=args.use_shell))
        case _:
            print(list(parse_config().items()))

def init(dirc):
    pit = Pit(dirc)
    if pit.exists():
        raise FileExistsError('.pit already exists')
    try:
        get_root_pit()
        log.error('Nesting pits, probbably bad?')
    except:
        pass

    write_default_config(pit.config_fn)
    Path(pit.root / 'index').touch()
    pit.object_store.mkdir()

def write_default_config(file='.pit/config'):
    path = Path(file).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    config = configparser.ConfigParser()
    print(path)
    config['core'] = {'url': str(path.parent / 'objects')}
    with open(path, 'w') as configfile:
        config.write(configfile)

def clone(old_pit, new_pit):
    ''' Copy the index of the url and create config to correspond to it '''
    try:
        get_root_pit()
        log.error('Nesting pits, probbably bad?')
    except:
        pass

    pit = Pit(new_pit)
    if pit.config_fn.exists():
        raise FileExistsError('.pit already exists')
    pit.root.mkdir(parents=True, exist_ok=True)

    pit.config['core'] = {'url': Path(old_pit) / '.pit' / 'objects'}

    # will make the necessary connections in the @property
    if pit.index is None:
        log.error("Can't read index %d" % str(pit.root))
        raise FileNotFoundError("Can't read other index")

    # update the config
    with open(pit.config_fn, 'w') as fd:
        pit.config.write(fd)

def add(pit, tosave):
    ''' Save the files to prevent accidental deletion
    Creates a hard-link into a content addressable directory and
        changes the permissions to be read only
    Also maintains an index file for easy navigation and lookup'''
    tosave = Path(tosave)
    if not pit.exists():
        log.error("pit doesn't exist")
        return

    log.info(f'Adding {tosave}...')

    # Get list of all files to save
    if tosave.is_dir():
        files = list(get_all_files(tosave))
    else:
        files = [tosave]

    # Save files
    for fn in files:
        # Be a little safe in what we save
        if not pit.verify_file(fn):
            continue

        # Write the file in content addressable fashion
        fn = fn.resolve()

        mode     = fn.stat().st_mode
        fhash    = hash_content(fn)
        rel_fn   = fn.relative_to(pit.root.parent)
        entry    = f'{mode} {fhash} {rel_fn}'
        print(f'Adding entry {entry}')

        new_path = pit.object_store / Path(fhash[:2]) / Path(fhash[2:])

        to_add = True
        for e in pit.index:
            _, index_hash, index_fn = e.strip().split()
            if fhash == index_hash:
                log.error('%s data already in index', fn)
                to_add = False
            if str(rel_fn) == index_fn:
                log.error('%s warning duplicate names in index!', fn)
                to_add = False

        if to_add:
            move(fn, new_path)
            pit.add_to_index(entry)

def checkout(pit, filename):
    filename = Path(filename).resolve().relative_to(pit.root.parent)
    log.debug('Checking out %s', filename)
    for index_line in pit.index:
        st_mode, fhash, path = index_line.split(maxsplit=3)
        path = Path(path.strip())
        if path != filename:
            continue
        saved_path = pit.object_store / fhash[:2] / fhash[2:]
        move(saved_path, pit.root.parent / filename)
        break

def get_all_files(dirname, ignore_hidden=True):
    for dirpath, dirnames, filenames in os.walk(dirname):
        if ignore_hidden:
            for dirname in dirnames.copy():
                if dirname.startswith('.'):
                    log.warning(f'Skipping hidden dir {dirname}')
                    del dirnames[dirnames.index(dirname)]
        for fn in filenames:
            yield Path(dirpath) / fn

def diff(file1, file2, outfile=None, use_shell=False):
    patch = io.BytesIO()
    if use_shell and shutil.which('diff') is not None:
        ret = subprocess.run(['diff', '-u', '-t', file1, file2], capture_output=True)
        patch.write(ret.stdout)
    else:
        with open(file1, 'rb') as fd1, open(file2, 'rb') as fd2:
            data1, data2 = fd1.readlines(), fd2.readlines()
        d = difflib.diff_bytes(difflib.unified_diff, data1, data2, file1.encode(), file2.encode(), file_mtime(file1).encode(), file_mtime(file2).encode())
        patch.writelines(d)
    patch.seek(0)
    wrapper = io.TextIOWrapper(patch)
    if outfile is None:
        return wrapper.read()
    with open(outfile, 'w') as outfd:
        outfd.write(wrapper.read())

def patch(file, patch):
    subprocess.run(['patch', file, patch])

def file_mtime(path):
    t = datetime.fromtimestamp(os.stat(path).st_mtime, timezone.utc)
    return t.astimezone().isoformat()

def move(from_path, to_path):
    log.info(f'Moving {from_path} -> {to_path}')
    if ':' in str(from_path) or ':' in str(to_path):
        host, path = str(to_path).split(':')
        subprocess.run(f'ssh {host} "mkdir -p $(dirname {path})"', shell=True, check=True)
        subprocess.run(f'scp {from_path} {to_path}', shell=True, check=True)
    else:
        to_path.parent.mkdir(parents=True, exist_ok=True)
        os.link(from_path, to_path)
        from_path.chmod(0o440)
        to_path.chmod(0o440)

def hash_content(file):
    log.info(f'Hashing {file}')
    path = Path(file)
    h = hashlib.sha256()
    with open(file, 'rb') as fd:
        h.update(fd.read(4096))
    return h.hexdigest()

def is_newer(file, dep):
    return os.stat(file).st_mtime > os.stat(dep).st_mtime

def parse_config(file='./.pit/config'):
    path = Path(file)
    config = configparser.ConfigParser()
    config.read(path)
    return config

def get_root_pit():
    ''' find if there is a pit from above here '''
    cwd = Path.cwd()
    root = None
    for p in [cwd, *cwd.parents]:
        pit = p / '.pit'
        if pit.exists():
            return pit.resolve()
    raise ValueError('No pit repo')

if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    parser = argparse.ArgumentParser(description='content addressable stuff')
    commands = parser.add_subparsers(title='subcommands', description='valid subcommands', dest='command')

    initcmd = commands.add_parser('init')

    clonecmd = commands.add_parser('clone')
    clonecmd.add_argument('url', help='location of .pit repo you want to clone')

    addcmd = commands.add_parser('add')
    addcmd.add_argument('path', help='file or directory to add')

    #  dep = commands.add_parser('dep')
    #  dep.add_argument('path', help='file or directory to check')
    #  dep.add_argument('dep', help='its dependencies')

    #  diffcmd = commands.add_parser('diff')
    #  diffcmd.add_argument('path')
    #  diffcmd.add_argument('cmp')
    #  diffcmd.add_argument('--use_shell', action='store_true')

    checkoutcmd = commands.add_parser('checkout')
    checkoutcmd.add_argument('filename')

    main(parser.parse_args())
