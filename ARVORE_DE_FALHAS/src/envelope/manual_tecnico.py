"""
Schema de dados estruturados para manuais tecnicos industriais.
"""
from typing import Dict, Optional

from .envelope import BaseDocumentData


class ManualTecnicoData(BaseDocumentData):
    nome_equipamento: Optional[str] = None
    fabricante: Optional[str] = None
    modelo: Optional[str] = None
    numero_serie: Optional[str] = None
    tipo_equipamento: Optional[str] = None
    tipo_manual: Optional[str] = None
    numero_manual: Optional[str] = None
    edicao: Optional[str] = None
    data_publicacao: Optional[str] = None
    idioma: Optional[str] = None
    aplicacoes: Optional[str] = None
    dados_tecnicos_principais: Optional[str] = None
    instrucoes_instalacao: Optional[str] = None
    instrucoes_operacao: Optional[str] = None
    instrucoes_manutencao: Optional[str] = None
    lista_pecas_resumo: Optional[str] = None
    advertencias_seguranca: Optional[str] = None
    normas_referenciadas: Optional[str] = None

    @classmethod
    def tool_schema_properties(cls) -> Dict[str, Dict]:
        return {
            "nome_equipamento": {"type": "string", "description": "Nome do equipamento"},
            "fabricante": {"type": "string", "description": "Fabricante/marca"},
            "modelo": {"type": "string", "description": "Modelo ou serie"},
            "numero_serie": {"type": "string", "description": "Numero de serie ou referencia do manual"},
            "tipo_equipamento": {"type": "string", "description": "Categoria do equipamento (ex: maquina_corte_laser, bomba, compressor)"},
            "tipo_manual": {"type": "string", "description": "Tipo do manual: manual_servico, manual_instalacao, lista_pecas, manual_operacao, manual_manutencao, brochura_tecnica"},
            "numero_manual": {"type": "string", "description": "Numero/codigo do manual"},
            "edicao": {"type": "string", "description": "Edicao ou revisao"},
            "data_publicacao": {"type": "string", "description": "Data de publicacao"},
            "idioma": {"type": "string", "description": "Idioma do manual"},
            "aplicacoes": {"type": "string", "description": "Aplicacoes do equipamento"},
            "dados_tecnicos_principais": {"type": "string", "description": "Especificacoes tecnicas principais (potencia, tensao, RPM, vazao, pressao, etc)"},
            "instrucoes_instalacao": {"type": "string", "description": "Resumo das instrucoes de instalacao"},
            "instrucoes_operacao": {"type": "string", "description": "Instrucoes de operacao (ligar, desligar, procedimentos)"},
            "instrucoes_manutencao": {"type": "string", "description": "Instrucoes de manutencao preventiva e corretiva"},
            "lista_pecas_resumo": {"type": "string", "description": "Resumo das submontagens principais da lista de pecas"},
            "advertencias_seguranca": {"type": "string", "description": "Advertencias e avisos de seguranca"},
            "normas_referenciadas": {"type": "string", "description": "Normas referenciadas (ISO, IEC, NBR)"},
        }
