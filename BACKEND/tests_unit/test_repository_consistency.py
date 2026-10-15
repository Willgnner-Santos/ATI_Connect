"""Offline checks for references that can drift during repository maintenance."""
import copy
import importlib.util
import re
import tomllib
from pathlib import Path
from urllib.parse import unquote

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


class CloudFormationLoader(yaml.SafeLoader):
    """Keep short-form intrinsics as tagged data; do not resolve AWS resources."""


def _intrinsic(loader, tag, node):
    if isinstance(node, yaml.ScalarNode):
        value = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node)
    else:
        value = loader.construct_mapping(node)
    return {tag: value}


CloudFormationLoader.add_multi_constructor('!', _intrinsic)


def load_template(text=None):
    return yaml.load(
        text if text is not None else (ROOT / 'template.yaml').read_text(encoding='utf-8'),
        Loader=CloudFormationLoader,
    )


def functions():
    return {
        name: resource['Properties']
        for name, resource in load_template()['Resources'].items()
        if resource['Type'] == 'AWS::Serverless::Function'
    }


def sam_routes():
    return {
        (event['Properties']['Method'].upper(), event['Properties']['Path'])
        for props in functions().values()
        for event in props.get('Events', {}).values()
        if event['Type'] == 'Api'
    }


def generator():
    path = ROOT / 'scripts/generate-local-template.py'
    spec = importlib.util.spec_from_file_location('local_template_generator', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_all_sam_handlers_exist():
    for name, props in functions().items():
        path = ROOT / props['CodeUri'] / (props['Handler'].split('.')[0] + '.py')
        assert path.is_file(), f'{name}: missing {path}'
    deployed = {p['CodeUri'].strip('/') for p in functions().values()}
    authored = {p.parent.relative_to(ROOT).as_posix() for p in (ROOT / '02_lambdas').rglob('handler.py')}
    assert deployed == authored


def test_openapi_operations_match_sam():
    api = yaml.safe_load((ROOT / 'docs/openapi.yaml').read_text(encoding='utf-8'))
    methods = {'get', 'post', 'put', 'delete', 'patch', 'head', 'options'}
    declared = {(m.upper(), p) for p, item in api['paths'].items() for m in item if m in methods}
    assert declared == sam_routes()


def test_readme_inventory_matches_templates():
    template = load_template()
    db = load_template((ROOT / 'infrastructure/cloudformation/active/02-dynamodb-saas.yml').read_text(encoding='utf-8'))
    assert len(functions()) == 45, 'Update README inventory after adding/removing a Lambda.'
    assert len(sam_routes()) == 41, 'Update README/OpenAPI inventory after changing routes.'
    assert sum(r['Type'] == 'AWS::DynamoDB::Table' for r in db['Resources'].values()) == 9
    assert sum(r['Type'] == 'AWS::SQS::Queue' for r in template['Resources'].values()) == 8
    assert template['Globals']['Function']['Runtime'] == 'python3.14'
    assert template['Globals']['Function']['Architectures'] == ['x86_64']


@pytest.mark.parametrize('script,variable,separators', [
    ('update-lambdas.ps1', 'lambdas', '/'),
    ('update-layer.ps1', 'functions', '-'),
])
def test_update_scripts_cover_every_sam_function(script, variable, separators):
    content = (ROOT / 'scripts' / script).read_text(encoding='utf-8-sig')
    block = re.search(r'\$' + variable + r'\s*=\s*@\((.*?)\)', content, flags=re.S)
    assert block, f'Missing ${variable} list'
    actual = re.findall(r'"([a-z0-9/-]+)"', block.group(1))
    if separators == '/':
        expected = {p['CodeUri'].removeprefix('02_lambdas/saas/').strip('/') for p in functions().values()}
    else:
        expected = {
            p['FunctionName']['Sub'].removeprefix('${Project}-saas-').removesuffix('-${Stage}')
            for p in functions().values()
        }
    assert len(actual) == len(set(actual)), 'Duplicate function in update script'
    assert set(actual) == expected


def test_layer_source_does_not_include_installed_dependencies():
    names = {p.name for p in (ROOT / '01_layers/plataforma_ati/python').iterdir()}
    assert names - {'__pycache__'} == {'plataforma_ati.py', 'notifications'}


def test_local_generator_resolves_layer_and_build_contract():
    source = (ROOT / 'template.yaml').read_text(encoding='utf-8')
    result = generator().render_local_template(source)
    local = load_template(result)
    assert '!Ref LayerArn' not in result
    assert local['Globals']['Function']['Layers'] == [{'Ref': 'PlataformaAtiLayerLocal'}]
    assert local['Parameters']['LayerArn']['Default'] == ''
    layer = local['Resources']['PlataformaAtiLayerLocal']
    assert layer['Metadata'] == {'BuildMethod': 'makefile', 'BuildArchitecture': 'x86_64'}
    assert layer['Properties']['CompatibleRuntimes'] == ['python3.14']
    assert layer['Properties']['ContentUri'] == '01_layers/plataforma_ati/'
    makefile = (ROOT / layer['Properties']['ContentUri'] / 'Makefile').read_text()
    assert 'build-PlataformaAtiLayerLocal:' in makefile
    assert 'python3.14 -m pip install' in makefile


def test_local_generator_preserves_application_resources():
    source = (ROOT / 'template.yaml').read_text(encoding='utf-8')
    original = load_template(source)
    local = load_template(generator().render_local_template(source))
    expected = copy.deepcopy(original['Resources'])
    env = expected['EquipmentNewFunction']['Properties']['Environment']['Variables']
    env['IOT_POLICY'] = {'Sub': 'plataforma-ati-iot-device-policy-${Stage}'}
    actual = dict(local['Resources'])
    actual.pop('PlataformaAtiLayerLocal')
    assert actual == expected


@pytest.mark.parametrize('old,new', [
    ('- !Ref LayerArn', '- !Ref OtherLayer'),
    ('IOT_POLICY: !Ref DeviceIoTPolicy', 'IOT_POLICY: missing'),
    ('Resources:\n', 'OtherResources:\n'),
])
def test_local_generator_fails_on_template_drift(old, new):
    source = (ROOT / 'template.yaml').read_text(encoding='utf-8')
    with pytest.raises(ValueError):
        generator().render_local_template(source.replace(old, new, 1))


def test_local_generator_rejects_generated_input():
    module = generator()
    source = (ROOT / 'template.yaml').read_text(encoding='utf-8')
    with pytest.raises(ValueError):
        module.render_local_template(module.render_local_template(source))


def test_sam_config_does_not_mix_bucket_options():
    config = tomllib.loads((ROOT / 'samconfig.toml').read_text(encoding='utf-8'))
    for name in ('default', 'sandbox', 'staging', 'production'):
        params = config[name]['deploy']['parameters']
        assert not (params.get('s3_bucket') and params.get('resolve_s3'))


def test_default_test_selection_is_offline():
    config = (ROOT / 'pytest.ini').read_text(encoding='utf-8')
    assert re.search(r'^testpaths\s*=\s*tests_unit\s*$', config, flags=re.M)


def test_maintenance_tools_are_kept_in_supported_locations():
    assert not (ROOT / 'test-push').exists()
    assert not (ROOT / 'iot_device_simulator.py').exists()
    assert not (ROOT / 'telemetry_simulator.py').exists()
    assert (ROOT / 'scripts/iot_device_simulator.py').is_file()
    assert (ROOT / 'scripts/telemetry_simulator.py').is_file()
    assert not (ROOT / 'infrastructure/cloudformation/legacy').exists()


def test_integration_fixture_invokes_built_template():
    fixture = (ROOT / 'tests/conftest.py').read_text(encoding='utf-8')
    assert '"--use-container"' in fixture
    assert '"--template-file", ".aws-sam/build/template.yaml"' in fixture


def test_documentation_relative_links_exist():
    docs = [ROOT / 'readme.md', ROOT / 'AGENTS.md', ROOT / 'infrastructure/README.md']
    docs += list((ROOT / 'docs').rglob('*.md'))
    docs += [ROOT / '01_layers/plataforma_ati/README.md']
    missing = []
    for doc in docs:
        text = doc.read_text(encoding='utf-8')
        for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)', text):
            if target.startswith(('https://', 'http://', 'mailto:', '#')):
                continue
            target = unquote(target.split('#', 1)[0])
            if target and not (doc.parent / target).exists():
                missing.append((str(doc.relative_to(ROOT)), target))
    assert not missing, missing


def test_function_names_match_incremental_update_naming():
    script = (ROOT / 'scripts/update-lambdas.ps1').read_text(encoding='utf-8')
    block = re.search(r'\$functionNameOverrides\s*=\s*@\{(.*?)\}', script, flags=re.S)
    assert block
    overrides = dict(re.findall(r'"([a-z0-9/-]+)"\s*=\s*"([a-z0-9-]+)"', block.group(1)))
    assert '$functionNameOverrides.ContainsKey($LambdaPath)' in script
    assert '$functionNameOverrides[$LambdaPath]' in script
    for props in functions().values():
        source_path = props['CodeUri'].removeprefix('02_lambdas/saas/').strip('/')
        suffix = overrides.get(source_path, source_path.replace('/', '-'))
        assert props['FunctionName'] == {'Sub': '${Project}-saas-' + suffix + '-${Stage}'}


def test_layer_publication_builds_before_publishing_and_uses_exact_version():
    script = (ROOT / 'scripts/update-layer.ps1').read_text(encoding='utf-8')
    assert script.index('sam build PlataformaAtiLayerLocal') < script.index('aws lambda publish-layer-version')
    assert '--use-container' in script
    assert '--version-number $layerVersion' in script
    assert 'list-layer-versions' not in script
    assert 'if ($failed -gt 0) { exit 1 }' in script
