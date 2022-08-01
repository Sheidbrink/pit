import pytest
import subprocess
import string
import random

from pit import init, parse_config, clone, Pit, hash_content, add, checkout

def test_all():
    test_here()
    test_local()
    test_remote()

def test_here():
    pass

def test_local():
    pass

def test_remote():
    pass

@pytest.fixture
def tmp_dir(tmp_path_factory):
    init_dir  = tmp_path_factory.mktemp('first')
    clone_dir = tmp_path_factory.mktemp('second')
    return init_dir, clone_dir


def test_init(tmp_dir):
    init_dir, _ = tmp_dir

    init(init_dir)
    with pytest.raises(Exception):
        init(init_dir)
    config_fn    = init_dir / '.pit' / 'config'
    index_fn     = init_dir / '.pit' / 'index'
    object_store = init_dir / '.pit' / 'objects'

    assert (init_dir / '.pit').exists()
    assert config_fn.exists()
    assert index_fn.exists()

    config = parse_config(config_fn)
    assert 'core' in config
    assert 'url' in config['core']
    assert str(object_store) == config['core']['url']

def test_clone(tmp_dir):
    init_dir, clone_dir = tmp_dir
    init(init_dir)
    clone(init_dir, clone_dir)
    cloned_pit = Pit(clone_dir)

    assert cloned_pit.object_store == init_dir / '.pit' / 'objects'

def test_add(tmp_dir):
    init_dir, _ = tmp_dir
    file = init_dir / 'file.txt'
    file.write_text('hello world!')

    init(init_dir)
    pit = Pit(init_dir)
    add(pit, file)
    assert file_in_pit(pit, file.relative_to(pit.root.parent))

def test_clone_add(tmp_dir):
    init_dir, clone_dir = tmp_dir
    init(init_dir)
    clone(init_dir, clone_dir)
    
    orig_pit  = Pit(init_dir)
    clone_pit = Pit(clone_dir)

    file = random_file(init_dir, 'file')
    file2 = random_file(clone_dir, 'file2')

    add(orig_pit, file)
    add(clone_pit, file2)

    file = file.relative_to(file.parent)
    file2 = file2.relative_to(file2.parent)
    assert file_in_pit(orig_pit, file) and file_in_pit(clone_pit, file)

def test_checkout(tmp_path):
    init(tmp_path)
    pit = Pit(tmp_path)

    file = random_file(tmp_path, 'file')
    add(pit, file)
    file.unlink()

    assert not file.exists()
    assert file_in_pit(pit, file, just_index=True)

    checkout(pit, file)

    assert file.exists()
    assert file_in_pit(pit, file)

def test_hash(tmp_path):
    file = random_file(tmp_path, 'file')
    digest = hash_content(file)
    shell_digest = subprocess.run(f'shasum -a 256 {file}', shell=True, capture_output=True)
    assert digest == shell_digest.stdout.split()[0].decode()

def random_file(file_dir, name):
    file = file_dir / name
    file.write_text(''.join(random.choices(string.ascii_letters, k=42)))
    return file

def file_in_pit(pit, file, just_index=False):
    if file.is_absolute():
        file = str(file.relative_to(pit.root.parent))
    else:
        file = str(file)
    for entry in pit.index:
        _, h, fn = entry.strip().split()
        if fn == file:
            if just_index or (pit.object_store / h[:2] / h[2:]).exists():
                return True
    return False
