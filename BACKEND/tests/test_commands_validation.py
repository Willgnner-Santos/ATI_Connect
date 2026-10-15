import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / '01_layers' / 'plataforma_ati' / 'python'))

import pytest
from plataforma_ati import validateCommands


def test_validate_commands_accepts_supported_types_and_constraints():
    commands = {
        'andar_destino': {'name': 'Ir para andar', 'dataType': 'int', 'resource': 'andar_destino', 'min': -2, 'max': 30},
        'porta': {'name': 'Controlar porta', 'dataType': 'string', 'resource': 'porta', 'allowedValues': ['abrir', 'fechar']},
        'emergencia': {'name': 'Modo emergência', 'dataType': 'bool', 'resource': 'emergencia'},
    }
    validateCommands(commands)
    assert commands['andar_destino']['variable'] == 'andar_destino'


@pytest.mark.parametrize('command', [
    {'name': 'x', 'dataType': 'decimal', 'resource': 'x'},
    {'name': 'x', 'dataType': 'string', 'resource': 'x', 'allowedValues': [1]},
    {'name': 'x', 'dataType': 'string', 'resource': 'x', 'min': 0},
    {'name': 'x', 'dataType': 'int', 'resource': 'x', 'min': 3, 'max': 1},
])
def test_validate_commands_rejects_invalid_definitions(command):
    with pytest.raises(Exception, match='400::'):
        validateCommands({'x': command})
