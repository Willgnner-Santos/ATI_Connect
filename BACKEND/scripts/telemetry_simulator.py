import streamlit as st
import boto3
from boto3.dynamodb.conditions import Key, Attr
import time
from datetime import datetime
import random
from decimal import Decimal
import iot_device_simulator as iot_sim

# --- Configurações Iniciais ---
st.set_page_config(page_title="Simulador de Telemetria Sandbox", layout="wide")
st.title("Simulador de Telemetria - Sandbox ATI")

# Configurações do Ambiente Sandbox
dynamodb = boto3.resource('dynamodb', region_name='sa-east-1')
table_prefix = "plataforma-ati-sandbox"

# Nomes das tabelas
ORG_TABLE = f"{table_prefix}-organizations"
TENANT_TABLE = f"{table_prefix}-tenants"
EQ_TABLE = f"{table_prefix}-tenant-equipment"
TEL_TABLE = f"{table_prefix}-tenant-equipment-telemetry"

# --- Funções de Acesso a Dados ---
@st.cache_data(ttl=60)
def get_organizations():
    try:
        table = dynamodb.Table(ORG_TABLE)
        response = table.query(
            IndexName='status-index',
            KeyConditionExpression=Key('status').eq('active'),
            FilterExpression=Attr('sk').eq('METADATA')
        )
        items = response.get('Items', [])
        return [item for item in items if item.get('organizationId') != 'PLATFORM']
    except Exception as e:
        st.error(f"Erro ao buscar organizações: {e}")
        return []

@st.cache_data(ttl=60)
def get_tenants(org_id):
    try:
        table = dynamodb.Table(TENANT_TABLE)
        response = table.query(
            KeyConditionExpression=Key('pk').eq(f"ORG#{org_id}#TENANTS")
        )
        return response.get('Items', [])
    except Exception as e:
        st.error(f"Erro ao buscar tenants: {e}")
        return []

@st.cache_data(ttl=60)
def get_equipment(tenant_id):
    try:
        table = dynamodb.Table(EQ_TABLE)
        response = table.query(
            KeyConditionExpression=Key('pk').eq(f"TENANT#{tenant_id}#EQUIPMENT")
        )
        return response.get('Items', [])
    except Exception as e:
        st.error(f"Erro ao buscar equipamentos: {e}")
        return []

def send_telemetry_payload(equipment_id, payload):
    try:
        table = dynamodb.Table(TEL_TABLE)
        timestamp_ms = int(time.time() * 1000)

        converted_payload = {
            k: Decimal(str(v)) if isinstance(v, float) else v
            for k, v in payload.items()
        }

        item = {
            'pk': f"EQUIPMENT#{equipment_id}",
            'sk': timestamp_ms,
            'data': converted_payload
        }

        table.put_item(Item=item)
        return True, timestamp_ms
    except Exception as e:
        return False, str(e)


@st.cache_resource
def get_iot_data_client():
    """Client MQTT cacheado entre reruns — describe_endpoint só roda uma vez por sessão."""
    return iot_sim.get_iot_data_client(iot_sim.REGION)


def build_reading_from_config(configs):
    """Gera uma leitura a partir dos valores base/ruído/probabilidade definidos na UI
    (mesmo formato dos configs da aba de lote), em vez dos defaults fixos por sensorType."""
    payload = {}
    for var_name, conf in configs.items():
        if conf['type'] == 'boolean':
            payload[var_name] = 1 if random.random() < (conf['prob'] / 100.0) else 0
        else:
            payload[var_name] = round(conf['base'] + random.uniform(-conf['noise'], conf['noise']), 2)
    return payload


# --- Interface Principal ---
col1, col2, col3 = st.columns(3)

# 1. Selecionar Organização (Cliente)
with col1:
    st.subheader("1. Empresa/Organização")
    orgs = get_organizations()
    if orgs:
        org_options = {org['organizationId']: org.get('name', org['organizationId']) for org in orgs}
        selected_org_id = st.selectbox("Selecione a Organização", options=list(org_options.keys()), format_func=lambda x: org_options[x])
    else:
        st.warning("Nenhuma organização encontrada.")
        selected_org_id = None

# 2. Selecionar Tenant (Filial)
with col2:
    st.subheader("2. Cliente/Tenant")
    selected_tenant_id = None
    if selected_org_id:
        tenants = get_tenants(selected_org_id)
        if tenants:
            tenant_options = {t.get('tenantId', t['sk'].replace('TENANT#', '')): t.get('name', t.get('tenantId', t['sk'])) for t in tenants}
            selected_tenant_id = st.selectbox("Selecione o Tenant", options=list(tenant_options.keys()), format_func=lambda x: tenant_options[x])
        else:
            st.warning("Nenhum tenant encontrado para esta organização.")

# 3. Selecionar Equipamento
with col3:
    st.subheader("3. Equipamento")
    selected_equipment = None
    if selected_tenant_id:
        equipments = get_equipment(selected_tenant_id)
        if equipments:
            eq_options = {eq.get('equipmentId', eq['sk'].replace('EQUIPMENT#', '')): eq for eq in equipments}
            selected_eq_id = st.selectbox("Selecione o Equipamento", options=list(eq_options.keys()), format_func=lambda x: eq_options[x].get('name', x))
            selected_equipment = eq_options.get(selected_eq_id)
        else:
            st.warning("Nenhum equipamento encontrado para este tenant.")


# --- Painel de Telemetria Dinâmico ---
st.divider()

if selected_equipment:
    st.header(f"Controle de Telemetria: {selected_equipment.get('name', selected_eq_id)}")

    sensors = selected_equipment.get('sensors', {})

    if not sensors:
        st.warning("Este equipamento não possui a propriedade 'sensors' cadastrada no banco. Não é possível gerar formulário de telemetria.")
    else:
        with st.expander("Sensores cadastrados para este equipamento (JSON)", expanded=False):
            st.json(sensors)

        tab1, tab2, tab3 = st.tabs([
            "Simular Valor Único",
            "Simular Série (Lote com Ruído)",
            "Simulação Contínua (MQTT real)"
        ])

        # TAB 1: VALOR ÚNICO
        with tab1:
            st.subheader("Simular Valores Manuais")
            with st.form("telemetry_form"):
                manual_inputs = {}
                # Agrupar em duas colunas
                cols = st.columns(2)

                for idx, (sensor_key, sensor_data) in enumerate(sensors.items()):
                    col = cols[idx % 2]
                    var_name = sensor_data.get('variable', sensor_key)
                    display_name = sensor_data.get('name', var_name)
                    s_type = sensor_data.get('sensorType', 'number')

                    with col:
                        if s_type == 'boolean':
                            manual_inputs[var_name] = st.selectbox(f"{display_name} ({var_name})", [0, 1], key=f"man_{var_name}")
                        else:
                            manual_inputs[var_name] = st.number_input(f"{display_name} ({var_name})", value=0.0, key=f"man_{var_name}")

                submitted = st.form_submit_button("Enviar Telemetria", type="primary")

                if submitted:
                    success, result = send_telemetry_payload(selected_eq_id, manual_inputs)
                    if success:
                        dt_str = datetime.fromtimestamp(result/1000).strftime('%Y-%m-%d %H:%M:%S')
                        st.success(f"Telemetria enviada com sucesso! (Timestamp: {dt_str})")
                    else:
                        st.error(f"Erro ao enviar: {result}")

        # TAB 2: LOTE COM RUÍDO
        with tab2:
            st.subheader("Simular Série (Geração de Histórico com Ruído)")
            st.write("Configurações de ruído baseadas nos sensores reais cadastrados:")

            with st.form("batch_telemetry_form"):
                st.write("---")
                col_b1, col_b2 = st.columns(2)
                with col_b1:
                    num_points = st.number_input("Quantidade de Registros", min_value=1, max_value=1000, value=50)
                with col_b2:
                    interval_sec = st.number_input("Intervalo entre registros (segundos)", min_value=1, max_value=3600, value=5)

                st.write("---")
                st.write("**Parâmetros por Sensor**")

                batch_configs = {}
                cols_b = st.columns(3)

                for idx, (sensor_key, sensor_data) in enumerate(sensors.items()):
                    col = cols_b[idx % 3]
                    var_name = sensor_data.get('variable', sensor_key)
                    display_name = sensor_data.get('name', var_name)
                    s_type = sensor_data.get('sensorType', 'number')

                    with col:
                        st.markdown(f"**{display_name}**")
                        if s_type == 'boolean':
                            prob_ativo = st.slider(f"Prob. de ser 1/Ativo (%) - {var_name}", 0, 100, 50, key=f"prob_{var_name}")
                            batch_configs[var_name] = {'type': 'boolean', 'prob': prob_ativo}
                        else:
                            base_val = st.number_input(f"Base - {var_name}", value=0.0, key=f"base_{var_name}")
                            noise_val = st.number_input(f"Ruído Máx (±) - {var_name}", min_value=0.0, value=1.0, key=f"noise_{var_name}")
                            batch_configs[var_name] = {'type': 'number', 'base': base_val, 'noise': noise_val}

                st.write("---")
                batch_submitted = st.form_submit_button("Gerar e Enviar Série", type="primary")

                if batch_submitted:
                    table = dynamodb.Table(TEL_TABLE)
                    current_time_ms = int(time.time() * 1000)

                    success_count = 0
                    error_msg = None
                    progress_bar = st.progress(0)

                    for i in range(num_points):
                        point_time_ms = current_time_ms - ((num_points - 1 - i) * interval_sec * 1000)
                        payload = {}

                        for var_name, conf in batch_configs.items():
                            if conf['type'] == 'boolean':
                                payload[var_name] = 1 if random.random() < (conf['prob'] / 100.0) else 0
                            else:
                                noise_rnd = random.uniform(-conf['noise'], conf['noise'])
                                val = conf['base'] + noise_rnd
                                payload[var_name] = Decimal(str(round(val, 2)))

                        item = {
                            'pk': f"EQUIPMENT#{selected_eq_id}",
                            'sk': point_time_ms,
                            'data': payload
                        }

                        try:
                            table.put_item(Item=item)
                            success_count += 1
                        except Exception as e:
                            error_msg = str(e)
                            break

                        progress_bar.progress((i + 1) / num_points)

                    if error_msg:
                        st.error(f"Erro após {success_count} envios: {error_msg}")
                    else:
                        st.success(f"{success_count} registros de telemetria enviados com sucesso!")

        # TAB 3: SIMULAÇÃO CONTÍNUA VIA MQTT REAL
        with tab3:
            st.subheader("Simular como um device de verdade (MQTT → IoT Rule → Router → DynamoDB)")
            st.caption(
                "Diferente das abas anteriores (que escrevem direto no banco), esta publica de "
                "verdade no tópico IoT do equipamento e passa pelo pipeline real."
            )

            topic = selected_equipment.get('topic')
            if not topic:
                st.warning("Este equipamento não possui 'topic' cadastrado (provisionamento IoT incompleto).")
            else:
                if "continuous_active" not in st.session_state:
                    st.session_state.continuous_active = False
                    st.session_state.continuous_sent = 0
                    st.session_state.continuous_log = []

                is_active = st.session_state.continuous_active
                same_equipment = st.session_state.get("continuous_equipment_id") == selected_eq_id

                interval = st.number_input(
                    "Intervalo entre publicações (segundos)",
                    min_value=5, max_value=3600, value=300, step=5,
                    key="continuous_interval",
                    disabled=is_active
                )

                st.write("**Valores por sensor** (base ± ruído para numéricos, probabilidade para booleanos)")
                cols_cfg = st.columns(3)
                continuous_configs = {}
                for idx, (sensor_key, sensor_data) in enumerate(sensors.items()):
                    col = cols_cfg[idx % 3]
                    var_name = sensor_data.get('variable', sensor_key)
                    display_name = sensor_data.get('name', var_name)
                    s_type = sensor_data.get('sensorType', 'number')

                    with col:
                        st.markdown(f"**{display_name}** (`{var_name}`)")
                        if s_type == 'boolean':
                            prob = st.slider(
                                "Prob. de ser 1/Ativo (%)", 0, 100, 50,
                                key=f"cont_prob_{var_name}", disabled=is_active
                            )
                            continuous_configs[var_name] = {'type': 'boolean', 'prob': prob}
                        else:
                            default_base, default_noise = iot_sim.SENSOR_DEFAULTS.get(s_type, iot_sim.DEFAULT_BASE_NOISE)
                            base_val = st.number_input(
                                "Valor base", value=default_base,
                                key=f"cont_base_{var_name}", disabled=is_active
                            )
                            noise_val = st.number_input(
                                "Ruído máx (±)", min_value=0.0, value=default_noise,
                                key=f"cont_noise_{var_name}", disabled=is_active
                            )
                            continuous_configs[var_name] = {'type': 'number', 'base': base_val, 'noise': noise_val}

                if not is_active:
                    if st.button("▶️ Iniciar simulação contínua", type="primary"):
                        st.session_state.continuous_active = True
                        st.session_state.continuous_equipment_id = selected_eq_id
                        st.session_state.continuous_topic = topic
                        st.session_state.continuous_configs = continuous_configs
                        st.session_state.continuous_sent = 0
                        st.session_state.continuous_log = []
                        st.rerun()
                else:
                    if st.button("⏹️ Parar simulação"):
                        st.session_state.continuous_active = False
                        st.rerun()

                    if not same_equipment:
                        st.warning(
                            f"Simulação ativa para outro equipamento (`{st.session_state.continuous_equipment_id}`). "
                            "Selecione-o de volta acima ou pare a simulação atual."
                        )
                    else:
                        st.info(f"🟢 Publicando em `{topic}` a cada {int(interval)}s")

                        @st.fragment(run_every=f"{int(interval)}s")
                        def _continuous_tick():
                            if not st.session_state.continuous_active:
                                return

                            payload = build_reading_from_config(st.session_state.continuous_configs)
                            ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                            try:
                                client = get_iot_data_client()
                                iot_sim.publish_telemetry(client, st.session_state.continuous_topic, payload)
                                st.session_state.continuous_sent += 1
                                line = f"[{ts}] #{st.session_state.continuous_sent} {payload}"
                            except Exception as e:
                                line = f"[{ts}] ERRO ao publicar: {e}"

                            st.session_state.continuous_log.insert(0, line)
                            st.session_state.continuous_log = st.session_state.continuous_log[:20]

                            st.metric("Leituras publicadas", st.session_state.continuous_sent)
                            for log_line in st.session_state.continuous_log:
                                st.text(log_line)

                        _continuous_tick()
else:
    st.info("Navegue pelas opções acima para selecionar um equipamento e iniciar a simulação.")
