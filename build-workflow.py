# Gera o workflow n8n a partir dos blocos de codigo JS (evita escapar JSON na mao).
# Rode:  python build-workflow.py
import json

JS_EXTRACT = r"""
const item = $input.first().json;
const data = item.data || {};
const key = data.key || {};
const remoteJid = key.remoteJid || '';
const phone = remoteJid.split('@')[0];
const fromMe = !!key.fromMe;
const msg = data.message || {};
const text = (
  msg.conversation ||
  (msg.extendedTextMessage && msg.extendedTextMessage.text) ||
  (msg.buttonsResponseMessage && msg.buttonsResponseMessage.selectedButtonId) ||
  (msg.listResponseMessage && msg.listResponseMessage.singleSelectReply && msg.listResponseMessage.singleSelectReply.selectedRowId) ||
  ''
).trim();

// Ignora o que nao for mensagem de texto vinda do cliente (ack, status, msg propria)
if (item.event !== 'messages.upsert' || fromMe || !phone || !text) {
  return [];
}

return [{ json: { phone, text } }];
"""

JS_PARSE_ROUTE = r"""
const inbound = $('Extract Inbound').first().json;
const rows = $input.all();
const sessionRow = rows.length ? rows[0].json : null;
const step = sessionRow ? sessionRow.step : 'menu';
const session = sessionRow && sessionRow.data
  ? (typeof sessionRow.data === 'string' ? JSON.parse(sessionRow.data) : sessionRow.data)
  : {};
const text = inbound.text;
const phone = inbound.phone;
const attempts = session.attempts || 0;

function cpfIsValid(cpfRaw) {
  const cpf = (cpfRaw || '').replace(/\D/g, '');
  if (cpf.length !== 11 || /^(\d)\1{10}$/.test(cpf)) return false;
  let sum = 0;
  for (let i = 0; i < 9; i++) sum += parseInt(cpf[i], 10) * (10 - i);
  let d1 = (sum * 10) % 11; if (d1 === 10) d1 = 0;
  if (d1 !== parseInt(cpf[9], 10)) return false;
  sum = 0;
  for (let i = 0; i < 10; i++) sum += parseInt(cpf[i], 10) * (11 - i);
  let d2 = (sum * 10) % 11; if (d2 === 10) d2 = 0;
  return d2 === parseInt(cpf[10], 10);
}

function cnpjIsValid(raw) {
  const c = (raw || '').replace(/\D/g, '');
  if (c.length !== 14 || /^(\d)\1{13}$/.test(c)) return false;
  function dv(base) {
    let pos = base.length - 7, sum = 0;
    for (let i = 0; i < base.length; i++) {
      sum += parseInt(base[i], 10) * pos--;
      if (pos < 2) pos = 9;
    }
    const r = sum % 11;
    return r < 2 ? 0 : 11 - r;
  }
  if (dv(c.slice(0, 12)) !== parseInt(c[12], 10)) return false;
  return dv(c.slice(0, 13)) === parseInt(c[13], 10);
}

// O SGP aceita CPF ou CNPJ no mesmo campo (cpfcnpj) - contratos PJ existem.
function docIsValid(v) {
  const d = (v || '').replace(/\D/g, '');
  if (d.length === 14) return cnpjIsValid(d);
  return cpfIsValid(d);
}

// SGP devolve dataNascimento em ISO (AAAA-MM-DD); o cliente digita DD/MM/AAAA.
// Normaliza os dois para DDMMAAAA antes de comparar.
function normDate(v) {
  if (!v) return '';
  const s = String(v).trim();
  let m = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (m) return m[3] + m[2] + m[1];
  m = s.match(/^(\d{2})\D?(\d{2})\D?(\d{4})/);
  if (m) return m[1] + m[2] + m[3];
  return s.replace(/\D/g, '');
}

const MENU = 'Ola! Sou o atendimento automatico.\n\n*1* - Alterar nome/senha do Wi-Fi\n*2* - Financeiro / 2a via de boleto\n*3* - Suporte tecnico\n*4* - Falar com atendente\n\nDigite o numero da opcao desejada.';

let reply_text = null;
let next_step = step;
let session_patch = {};
let sgp_action = 'none';
let sgp_payload = {};

// "menu" digitado a qualquer momento reinicia o atendimento
if (/^(menu|sair|voltar|0)$/i.test(text) && step !== 'menu') {
  return [{ json: { phone, text, step, session,
    reply_text: MENU, next_step: 'menu',
    session_patch: { reset: true }, sgp_action: 'none', sgp_payload: {} } }];
}

switch (step) {
  case 'menu': {
    if (text === '1') {
      reply_text = 'Para sua seguranca, informe o CPF/CNPJ do titular da conta (somente numeros):';
      next_step = 'awaiting_cpf';
      session_patch = { attempts: 0 };
    } else if (['2', '3', '4'].includes(text)) {
      reply_text = 'Essa opcao ainda esta em construcao. Digite *1* para alterar seu Wi-Fi.';
      next_step = 'menu';
    } else {
      reply_text = MENU;
      next_step = 'menu';
    }
    break;
  }

  case 'awaiting_cpf': {
    if (!docIsValid(text)) {
      const n = attempts + 1;
      if (n >= 3) {
        reply_text = 'Nao consegui validar seu documento. Vou te transferir para um atendente humano.';
        next_step = 'human_handoff';
        session_patch = { attempts: 0 };
      } else {
        reply_text = 'CPF/CNPJ invalido. Digite novamente, apenas numeros (tentativa ' + n + '/3):';
        next_step = 'awaiting_cpf';
        session_patch = { attempts: n };
      }
    } else {
      sgp_action = 'lookup_cpf';
      sgp_payload = { cpf: text.replace(/\D/g, '') };
      session_patch = { cpf: text.replace(/\D/g, ''), attempts: 0 };
    }
    break;
  }

  case 'awaiting_contract_choice': {
    const opcoes = session.contract_options || [];
    const idx = parseInt(text, 10);
    if (!idx || idx < 1 || idx > opcoes.length) {
      reply_text = 'Opcao invalida. Responda com o numero do contrato desejado (1 a ' + opcoes.length + '):';
      next_step = 'awaiting_contract_choice';
    } else {
      const esc = opcoes[idx - 1];
      session_patch = { contrato: esc.contrato, contract_options: undefined };
      if (session.second_factor_pending) {
        reply_text = 'Para confirmar sua identidade, informe a data de nascimento do titular (DD/MM/AAAA):';
        next_step = 'awaiting_second_factor';
        session_patch.attempts = 0;
      } else {
        reply_text = 'Contrato selecionado. Qual sera o novo nome (SSID) da sua rede Wi-Fi?';
        next_step = 'awaiting_ssid';
      }
    }
    break;
  }

  case 'awaiting_second_factor': {
    const alvo = normDate(session.second_factor_target);
    const resp = normDate(text);
    if (alvo && resp && alvo === resp) {
      reply_text = 'Confirmado! Qual sera o novo nome (SSID) da sua rede Wi-Fi?';
      next_step = 'awaiting_ssid';
      session_patch = { attempts: 0, second_factor_target: undefined, second_factor_pending: undefined };
    } else {
      const n = attempts + 1;
      if (n >= 3) {
        reply_text = 'Nao consegui confirmar sua identidade. Vou te transferir para um atendente humano.';
        next_step = 'human_handoff';
        session_patch = { attempts: 0, second_factor_target: undefined };
      } else {
        reply_text = 'Data nao confere. Envie no formato DD/MM/AAAA (tentativa ' + n + '/3):';
        next_step = 'awaiting_second_factor';
        session_patch = { attempts: n };
      }
    }
    break;
  }

  case 'awaiting_ssid': {
    // SSID: 1-32 chars, sem caracteres de controle
    if (text.length < 1 || text.length > 32) {
      reply_text = 'O nome da rede deve ter entre 1 e 32 caracteres. Envie novamente:';
      next_step = 'awaiting_ssid';
    } else if (/[\x00-\x1f]/.test(text)) {
      reply_text = 'O nome da rede tem caracteres invalidos. Envie novamente:';
      next_step = 'awaiting_ssid';
    } else {
      reply_text = 'Nome definido como "' + text + '".\n\nAgora envie a nova senha do Wi-Fi (8 a 63 caracteres):';
      next_step = 'awaiting_password';
      session_patch = { ssid_new: text };
    }
    break;
  }

  case 'awaiting_password': {
    // WPA2/WPA3-PSK exige 8-63 caracteres ASCII imprimiveis
    if (text.length < 8 || text.length > 63) {
      reply_text = 'A senha precisa ter entre 8 e 63 caracteres. Envie novamente:';
      next_step = 'awaiting_password';
    } else if (!/^[\x20-\x7e]+$/.test(text)) {
      reply_text = 'A senha so pode ter letras, numeros e simbolos comuns (sem acentos/emoji). Envie novamente:';
      next_step = 'awaiting_password';
    } else {
      sgp_action = 'definir_wifi';
      sgp_payload = { contrato: session.contrato, ssid: session.ssid_new, senha: text };
    }
    break;
  }

  case 'human_handoff': {
    reply_text = 'Voce esta na fila de atendimento humano. Em breve alguem falara com voce por aqui.\n\nDigite *menu* para voltar ao inicio.';
    next_step = 'human_handoff';
    break;
  }

  default: {
    reply_text = MENU;
    next_step = 'menu';
  }
}

return [{ json: { phone, text, step, session, reply_text, next_step, session_patch, sgp_action, sgp_payload } }];
"""

JS_PROC_CPF = r"""
const prev = $('Parse & Route').first().json;
const resp = $input.first().json;

let reply_text, next_step;
const session_patch = Object.assign({}, prev.session_patch);

function last8(v) { return String(v || '').replace(/\D/g, '').slice(-8); }

// Resposta do SGP: { msg, contratos: [ ... ] }
const contratos = Array.isArray(resp && resp.contratos) ? resp.contratos : [];
// contratoStatus: 1=Ativo, 2=Inativo, 4=Suspenso
const ativos = contratos.filter(function (c) { return c.contratoStatus === 1; });

if (contratos.length === 0) {
  reply_text = 'Nao encontrei nenhum contrato com esse CPF. Confira o numero ou digite *4* para falar com um atendente.';
  next_step = 'menu';
} else if (ativos.length === 0) {
  reply_text = 'Localizei seu cadastro, mas nao ha contrato ativo no momento. Vou te transferir para um atendente.';
  next_step = 'human_handoff';
} else {
  const ref = ativos[0];

  // ---- Segundo fator: o numero do WhatsApp bate com algum telefone do cadastro? ----
  const telefones = [];
  ativos.forEach(function (c) {
    (c.telefones || []).forEach(function (t) { if (t && t.contato) telefones.push(t.contato); });
  });
  const telefoneBate = telefones.some(function (t) { return last8(t) && last8(t) === last8(prev.phone); });

  session_patch.nome = ref.razaoSocial || '';

  if (ativos.length === 1) {
    session_patch.contrato = ref.contratoId;
  } else {
    // Cliente com mais de um contrato ativo: precisa escolher qual
    session_patch.contract_options = ativos.slice(0, 9).map(function (c) {
      return { contrato: c.contratoId, label: (c.servico_plano || c.planointernet || 'Plano') + ' - ' + (c.endereco_logradouro || '') + ' ' + (c.endereco_numero || '') };
    });
  }

  if (telefoneBate) {
    if (ativos.length === 1) {
      reply_text = 'CPF confirmado' + (ref.razaoSocial ? ', ' + ref.razaoSocial : '') + '! Qual sera o novo nome (SSID) da sua rede Wi-Fi?';
      next_step = 'awaiting_ssid';
    } else {
      reply_text = 'Voce tem mais de um contrato ativo. Qual deles?\n\n' +
        session_patch.contract_options.map(function (o, i) { return '*' + (i + 1) + '* - ' + o.label; }).join('\n');
      next_step = 'awaiting_contract_choice';
    }
  } else {
    // Numero nao cadastrado -> exige data de nascimento
    const nasc = ref.dataNascimento || '';
    if (!nasc) {
      reply_text = 'Nao consegui confirmar sua identidade automaticamente. Vou te transferir para um atendente.';
      next_step = 'human_handoff';
    } else if (ativos.length === 1) {
      session_patch.second_factor_target = nasc;
      session_patch.attempts = 0;
      reply_text = 'Esse numero nao e o cadastrado no contrato. Para confirmar que e voce, informe a data de nascimento do titular (DD/MM/AAAA):';
      next_step = 'awaiting_second_factor';
    } else {
      session_patch.second_factor_target = nasc;
      session_patch.second_factor_pending = true;
      reply_text = 'Voce tem mais de um contrato ativo. Qual deles?\n\n' +
        session_patch.contract_options.map(function (o, i) { return '*' + (i + 1) + '* - ' + o.label; }).join('\n');
      next_step = 'awaiting_contract_choice';
    }
  }
}

return [{ json: Object.assign({}, prev, { reply_text: reply_text, next_step: next_step, session_patch: session_patch }) }];
"""

JS_PROC_WIFI = r"""
const prev = $('Parse & Route').first().json;
const resp = $input.first().json;

let reply_text, next_step;
const session_patch = Object.assign({}, prev.session_patch);

// Resposta do SGP: { msg, success }
const sucesso = !!(resp && resp.success === true);

if (sucesso) {
  reply_text = 'Pronto! Sua rede Wi-Fi foi atualizada:\n\n*Nome:* ' + prev.sgp_payload.ssid +
    '\n\nO roteador pode levar alguns minutos para aplicar. Seus aparelhos vao precisar conectar de novo com a nova senha.';
  next_step = 'menu';
  // limpa dados sensiveis da sessao
  session_patch.cpf = undefined;
  session_patch.contrato = undefined;
  session_patch.ssid_new = undefined;
  session_patch.nome = undefined;
} else {
  const msg = (resp && resp.msg) ? String(resp.msg) : '';
  if (/Gerenciador de CPE/i.test(msg)) {
    reply_text = 'Seu roteador nao esta habilitado para configuracao remota. Vou te transferir para um atendente resolver isso.';
    next_step = 'human_handoff';
  } else {
    reply_text = 'Nao consegui aplicar a alteracao agora. Tente novamente em alguns minutos ou digite *4* para falar com um atendente.';
    next_step = 'menu';
  }
}

return [{ json: Object.assign({}, prev, {
  reply_text: reply_text,
  next_step: next_step,
  session_patch: session_patch,
  _audit: {
    phone: prev.phone,
    cpf: prev.session.cpf,
    contrato: prev.sgp_payload.contrato,
    ssid_novo: prev.sgp_payload.ssid,
    sucesso: sucesso,
    resposta_sgp: resp,
  },
}) }];
"""

JS_PERSIST = r"""
const item = $input.first().json;
const patch = item.session_patch || {};
// patch.reset limpa a sessao inteira (cliente digitou "menu")
const merged = patch.reset ? {} : Object.assign({}, item.session || {}, patch);
delete merged.reset;
Object.keys(merged).forEach(function (k) { if (merged[k] === undefined) delete merged[k]; });

return [{
  json: {
    phone: item.phone,
    step: item.next_step,
    data: JSON.stringify(merged),
    reply_text: item.reply_text,
    audit: item._audit ? JSON.stringify(item._audit) : null,
  }
}];
"""


def code_node(node_id, name, js, pos):
    return {
        "parameters": {"jsCode": js.strip()},
        "id": node_id, "name": name,
        "type": "n8n-nodes-base.code", "typeVersion": 2, "position": pos,
    }


PG_CRED = {"postgres": {"id": "REPLACE_ME", "name": "Postgres - botSgp"}}

nodes = [
    {
        "parameters": {"httpMethod": "POST", "path": "evolution-inbound",
                       "responseMode": "onReceived", "options": {}},
        "id": "webhook-evolution", "name": "Webhook Evolution API",
        "type": "n8n-nodes-base.webhook", "typeVersion": 2, "position": [0, 0],
    },
    code_node("code-extract", "Extract Inbound", JS_EXTRACT, [200, 0]),
    {
        "parameters": {
            "operation": "executeQuery",
            "query": "SELECT step, data FROM wa_sessions WHERE phone = $1",
            "options": {"queryReplacement": "={{ [$json.phone] }}"},
        },
        "id": "pg-get", "name": "Get Session",
        "type": "n8n-nodes-base.postgres", "typeVersion": 2.4,
        "position": [400, 0], "credentials": PG_CRED,
    },
    code_node("code-route", "Parse & Route", JS_PARSE_ROUTE, [600, 0]),
    {
        "parameters": {
            "rules": {"values": [
                {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict"},
                                "conditions": [{"leftValue": "={{ $json.sgp_action }}", "rightValue": "lookup_cpf",
                                                "operator": {"type": "string", "operation": "equals"}}],
                                "combinator": "and"},
                 "renameOutput": True, "outputKey": "lookup_cpf"},
                {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict"},
                                "conditions": [{"leftValue": "={{ $json.sgp_action }}", "rightValue": "definir_wifi",
                                                "operator": {"type": "string", "operation": "equals"}}],
                                "combinator": "and"},
                 "renameOutput": True, "outputKey": "definir_wifi"},
            ]},
            "options": {"fallbackOutput": "extra", "renameFallbackOutput": "sem_chamada"},
        },
        "id": "switch-action", "name": "Precisa chamar o SGP?",
        "type": "n8n-nodes-base.switch", "typeVersion": 3.2, "position": [800, 0],
    },
    # ---- SGP: consulta cliente por CPF ----
    {
        "parameters": {
            "method": "POST",
            "url": "={{ $env.SGP_API_URL }}/api/ura/consultacliente/",
            "sendBody": True, "specifyBody": "json",
            "jsonBody": "={{ JSON.stringify({ app: $env.SGP_APP_NAME, token: $env.SGP_API_TOKEN, cpfcnpj: $json.sgp_payload.cpf }) }}",
            "options": {"response": {"response": {"neverError": True}}, "timeout": 20000},
        },
        "id": "http-lookup", "name": "SGP - Consultar Cliente",
        "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [1000, -160],
    },
    code_node("code-proc-cpf", "Processar Consulta CPF", JS_PROC_CPF, [1200, -160]),
    # ---- SGP: define wifi (2.4GHz + 5GHz com o mesmo nome/senha) ----
    {
        "parameters": {
            "method": "POST",
            "url": "={{ $env.SGP_API_URL }}/api/ura/cpemanage/",
            "sendBody": True, "contentType": "form-urlencoded",
            "bodyParameters": {"parameters": [
                {"name": "token", "value": "={{ $env.SGP_API_TOKEN }}"},
                {"name": "app", "value": "={{ $env.SGP_APP_NAME }}"},
                {"name": "contrato", "value": "={{ $json.sgp_payload.contrato }}"},
                {"name": "novo_ssid", "value": "={{ $json.sgp_payload.ssid }}"},
                {"name": "nova_senha", "value": "={{ $json.sgp_payload.senha }}"},
                {"name": "novo_ssid_5g", "value": "={{ $json.sgp_payload.ssid }}"},
                {"name": "nova_senha_5g", "value": "={{ $json.sgp_payload.senha }}"},
            ]},
            "options": {"response": {"response": {"neverError": True}}, "timeout": 30000},
        },
        "id": "http-wifi", "name": "SGP - Definir Wifi (CPE Manage)",
        "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [1000, 160],
    },
    code_node("code-proc-wifi", "Processar Definir Wifi", JS_PROC_WIFI, [1200, 160]),
    code_node("code-persist", "Preparar Persistencia", JS_PERSIST, [1400, 0]),
    {
        "parameters": {
            "operation": "executeQuery",
            "query": ("INSERT INTO wa_sessions (phone, step, data, updated_at)\n"
                      "VALUES ($1, $2, $3::jsonb, now())\n"
                      "ON CONFLICT (phone) DO UPDATE\n"
                      "  SET step = EXCLUDED.step, data = EXCLUDED.data, updated_at = now();"),
            "options": {"queryReplacement": "={{ [$json.phone, $json.step, $json.data] }}"},
        },
        "id": "pg-upsert", "name": "Upsert Session",
        "type": "n8n-nodes-base.postgres", "typeVersion": 2.4,
        "position": [1600, 0], "credentials": PG_CRED,
    },
    {
        "parameters": {
            "conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose"},
                           "conditions": [{"leftValue": "={{ $('Preparar Persistencia').first().json.audit }}",
                                           "rightValue": "",
                                           "operator": {"type": "string", "operation": "notEmpty", "singleValue": True}}],
                           "combinator": "and"},
            "options": {},
        },
        "id": "if-audit", "name": "Tem auditoria?",
        "type": "n8n-nodes-base.if", "typeVersion": 2.2, "position": [1800, 0],
    },
    {
        "parameters": {
            "operation": "executeQuery",
            "query": ("INSERT INTO wa_wifi_change_log (phone, cpf, contrato_id, ssid_novo, sucesso, resposta_sgp)\n"
                      "SELECT a->>'phone', a->>'cpf', a->>'contrato', a->>'ssid_novo',\n"
                      "       (a->>'sucesso')::boolean, a->'resposta_sgp'\n"
                      "FROM (SELECT $1::jsonb AS a) t;"),
            "options": {"queryReplacement": "={{ [$('Preparar Persistencia').first().json.audit] }}"},
        },
        "id": "pg-audit", "name": "Gravar Auditoria",
        "type": "n8n-nodes-base.postgres", "typeVersion": 2.4,
        "position": [2000, -120], "credentials": PG_CRED,
    },
    {
        "parameters": {
            "method": "POST",
            "url": "={{ $env.EVOLUTION_API_URL }}/message/sendText/{{ $env.EVOLUTION_INSTANCE }}",
            "sendBody": True, "specifyBody": "json",
            "jsonBody": "={{ JSON.stringify({ number: $('Preparar Persistencia').first().json.phone, text: $('Preparar Persistencia').first().json.reply_text }) }}",
            "sendHeaders": True,
            "headerParameters": {"parameters": [{"name": "apikey", "value": "={{ $env.EVOLUTION_API_KEY }}"}]},
            "options": {"timeout": 20000},
        },
        "id": "http-send", "name": "Evolution - Enviar Resposta",
        "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [2200, 0],
    },
]

connections = {
    "Webhook Evolution API": {"main": [[{"node": "Extract Inbound", "type": "main", "index": 0}]]},
    "Extract Inbound": {"main": [[{"node": "Get Session", "type": "main", "index": 0}]]},
    "Get Session": {"main": [[{"node": "Parse & Route", "type": "main", "index": 0}]]},
    "Parse & Route": {"main": [[{"node": "Precisa chamar o SGP?", "type": "main", "index": 0}]]},
    "Precisa chamar o SGP?": {"main": [
        [{"node": "SGP - Consultar Cliente", "type": "main", "index": 0}],
        [{"node": "SGP - Definir Wifi (CPE Manage)", "type": "main", "index": 0}],
        [{"node": "Preparar Persistencia", "type": "main", "index": 0}],
    ]},
    "SGP - Consultar Cliente": {"main": [[{"node": "Processar Consulta CPF", "type": "main", "index": 0}]]},
    "Processar Consulta CPF": {"main": [[{"node": "Preparar Persistencia", "type": "main", "index": 0}]]},
    "SGP - Definir Wifi (CPE Manage)": {"main": [[{"node": "Processar Definir Wifi", "type": "main", "index": 0}]]},
    "Processar Definir Wifi": {"main": [[{"node": "Preparar Persistencia", "type": "main", "index": 0}]]},
    "Preparar Persistencia": {"main": [[{"node": "Upsert Session", "type": "main", "index": 0}]]},
    "Upsert Session": {"main": [[{"node": "Tem auditoria?", "type": "main", "index": 0}]]},
    "Tem auditoria?": {"main": [
        [{"node": "Gravar Auditoria", "type": "main", "index": 0}],
        [{"node": "Evolution - Enviar Resposta", "type": "main", "index": 0}],
    ]},
    "Gravar Auditoria": {"main": [[{"node": "Evolution - Enviar Resposta", "type": "main", "index": 0}]]},
}

wf = {
    "name": "WhatsApp Autoatendimento Wi-Fi (Evolution API + SGP)",
    "nodes": nodes,
    "connections": connections,
    "active": False,
    "settings": {"executionOrder": "v1"},
    "pinData": {},
}

out = "n8n/workflow-wifi-selfservice.json"
with open(out, "w", encoding="utf-8") as f:
    json.dump(wf, f, ensure_ascii=False, indent=2)
print("gerado:", out, "| nodes:", len(nodes))
