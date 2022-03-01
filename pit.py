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

def main(args):
    print(args)
    config = parse_config()
    match args.command:
        case 'init':
            write_default_config()
        case 'add':
            log.debug(args.path)
            fhash = hash_content(args.path)
            log.debug(fhash)
            new_path = Path(config['core']['url']) / fhash[:2] / fhash[2:]
            log.debug(new_path)
            move(args.path, new_path)
        case 'dep':
            print(is_newer(args.path, args.dep))
        case 'diff':
            print(diff(args.path, args.cmp, use_shell=args.use_shell))
        case 'save':
            save(args.tosave, config['core']['url'])
        case 'restore':
            restore(args.torestore, config['core']['url'])
        case _:
            print(list(parse_config().items()))

def save(tosave, savedir):
    ''' Save the files to prevent accidental deletion
    Creates a hard-link into a content addressable directory and
        changes the permissions to be read only
    Also maintains an index file for easy navigation and lookup'''
    tosave, savedir = Path(tosave).resolve(), Path(savedir).resolve()
    log.info(f'Saving {tosave} into {savedir}')
    # Make sure the index exists
    index_path = savedir / 'index'
    if not index_path.exists():
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.touch()
    # Get list of all files to save
    if tosave.is_dir():
        files = list(get_all_files(tosave))
    else:
        files = [tosave]
    with open(index_path, 'r+') as indexfd:
        # Read old index
        index = indexfd.readlines()
        # Save files
        for fn in files:
            # Be a little safe in what we save
            if savedir.parent in fn.parents:
                log.warning(f'Refusing to save file {fn} from savedir')
                continue
            if fn == Path(__file__).resolve():
                log.warning(f'Refusing to save myself {fn}')
                continue
            if fn.is_symlink():
                log.warning(f'Refusing to backup symlink {fn}')
                continue
            # Write the file in content addressable fashion
            fn = fn.resolve()
            log.info(f'Hashing {fn}')
            fhash = hash_content(fn)
            new_path = savedir / fhash[:2] / fhash[2:]

            log.info(f'Moving {fn} -> {new_path}')
            mode = fn.stat().st_mode
            move(fn, new_path)
            # Update the index
            time = datetime.fromtimestamp(fn.stat().st_mtime).isoformat()
            entry = f'{mode} {fhash} {fn}\n'
            if entry not in index:
                # Write out the index
                indexfd.write(entry)

def restore(filename, savedir):
    # TODO delete from the save if theres no more in the index?
    filename, savedir = Path(filename).absolute(), Path(savedir).resolve()
    index_path = savedir / 'index'
    with open(index_path, 'r') as indexfd:
        index = indexfd.readlines()
    for index_line in index:
        st_mode, fhash, path = index_line.split(maxsplit=3)
        path = Path(path.strip())
        if path != filename:
            continue
        path = Path(path)
        saved_path = savedir / fhash[:2] / fhash[2:]

        log.info(f'Restoring {path} from {saved_path}')
        try:
            shutil.copy(saved_path, path)
        except shutil.SameFileError:
            os.unlink(path)
            shutil.copy(saved_path, path)
        path.chmod(int(st_mode))
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

def move(orig_path, backup_path):
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(orig_path, backup_path)
    #  shutil.copy2(orig_path, backup_path)
    os.symlink(backup_path, orig_path)
    backup_path.chmod(0o440)

def hash_content(file):
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

def write_default_config(file='./.pit/config'):
    path = Path(file)
    path.parent.mkdir(parents=True, exist_ok=True)
    config = configparser.ConfigParser()
    config['core'] = {'url': str(Path('./.pit/objects'))}
    with open(path, 'w') as configfile:
        config.write(configfile)

if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    parser = argparse.ArgumentParser(description='content addressable stuff')
    commands = parser.add_subparsers(title='subcommands', description='valid subcommands', dest='command')

    init = commands.add_parser('init')

    add = commands.add_parser('add')
    add.add_argument('path', help='file or directory to add')

    dep = commands.add_parser('dep')
    dep.add_argument('path', help='file or directory to check')
    dep.add_argument('dep', help='its dependencies')

    diffcmd = commands.add_parser('diff')
    diffcmd.add_argument('path')
    diffcmd.add_argument('cmp')
    diffcmd.add_argument('--use_shell', action='store_true')

    savecmd = commands.add_parser('save')
    savecmd.add_argument('tosave')

    restorecmd = commands.add_parser('restore')
    restorecmd.add_argument('torestore')

    main(parser.parse_args())
