# Gera o workflow n8n a partir dos blocos de codigo JS (evita escapar JSON na mao).
# Rode:  python3 build-workflow.py
import json

# ---------------------------------------------------------------- Extract
JS_EXTRACT = r"""
const raw = $input.first().json;
// O node de Webhook entrega o corpo da requisicao dentro de `body`. O
// fallback para a raiz cobre chamadas diretas ao node (testes e replay).
const item = raw.body || raw;
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
// e conversas de grupo (o bot atende so no privado).
if (item.event !== 'messages.upsert' || fromMe || !phone || !text || remoteJid.endsWith('@g.us')) {
  return [];
}

return [{ json: { phone, text } }];
"""

# ---------------------------------------------------------------- Parse & Route
JS_PARSE_ROUTE = r"""
const inbound = $('Extract Inbound').first().json;
const rows = $input.all();
const sessionRow = rows.length ? rows[0].json : null;
const step = (sessionRow && sessionRow.step) || 'menu';
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

// Aplicar Wi-Fi de verdade exige um Gerenciador de CPE (ACS/TR-069) cadastrado
// no SGP. Sem ele o SGP responde "O Servico de internet nao possui Gerenciador
// de CPE configurado" em TODA chamada. Como implantar ACS depende de
// provisionar as ONUs na OLT - trabalho do provedor, nao nosso - o modulo tem
// tres modos:
//
//   acs      aplica pelo cpemanage do SGP. Exige Gerenciador de CPE cadastrado.
//   genieacs aplica falando DIRETO com a NBI do GenieACS, sem passar pelo SGP.
//            Mesmo pre-requisito de campo (ONU provisionada apontando para o
//            ACS), mas dispensa cadastrar o Gerenciador de CPE no SGP - e,
//            como o n8n e o GenieACS rodam na mesma VM, dispensa tambem expor
//            a NBI na internet, que e o que o caminho pelo SGP obriga (o SGP
//            e SaaS: quem chamaria a NBI seria a nuvem da TSMX).
//            Em troca, o mapeamento de parametro por modelo passa a ser nosso:
//            veja "Montar Tarefa Wifi".
//   olt      aplica falando com a propria OLT, por OMCI, sem ACS nenhum.
//            E o caminho que funcionou em campo: a OLT escreve o SSID e a
//            senha direto na ONU pela fibra - sem TR-069, sem VLAN de
//            gerencia, sem DHCP. Exige que a ONU esteja num perfil (onu-type)
//            que declare as portas Wi-Fi das duas bandas.
//            O bot nao fala com a OLT: fala com o servico em olt-wifi/, que e
//            quem guarda a credencial. Ver OLT_WIFI_URL no .env.example.
//   chamado  coleta o que o cliente quer e abre uma ocorrencia no SGP para a
//            equipe aplicar. Nao automatiza, mas resolve HOJE: do lado do
//            cliente o atendimento e o mesmo, e a equipe recebe um pedido
//            estruturado em vez de uma ligacao.
//   off      tira a opcao do menu.
//
// O modo 'chamado' existe porque a alternativa real nao era "esperar o ACS", e
// sim o cliente ligar para o suporte - o que ja acontece, so que sem registro.
const WIFI_MODO = String($env.WIFI_MODO || 'acs').trim().toLowerCase();
const WIFI_ON = WIFI_MODO !== 'off';

// Trocar o NOME da rede so faz sentido onde o caminho de aplicacao aceita o que
// as pessoas escrevem. Pela OLT o comando nao aceita espaco no nome - e nome de
// Wi-Fi com espaco e exatamente o que a maioria digita. Em vez de obrigar todo
// mundo a hifenizar, da para oferecer so a troca de senha, que e o pedido mais
// comum e nao tem essa restricao.
const WIFI_NOME_ON = String($env.WIFI_PERMITE_NOME || 'true').trim().toLowerCase() !== 'false';

const MENU = 'Olá! Sou o atendimento automático.\n\n' +
  (WIFI_ON ? (WIFI_NOME_ON ? '*1* - Alterar nome/senha do Wi-Fi\n'
                           : '*1* - Alterar a senha do Wi-Fi\n') : '') +
  '*2* - 2ª via de boleto\n' +
  '*3* - Abrir chamado de suporte\n' +
  '*4* - Diagnóstico da minha conexão\n' +
  '*5* - Falar com atendente\n\n' +
  'Digite o número da opção desejada.';

// Identidade validada vale por uma janela curta. O cliente costuma resolver
// duas coisas na mesma conversa (ver o boleto e depois abrir um chamado), e
// repetir CPF + data de nascimento a cada modulo e atrito puro - ninguem
// termina o atendimento. Passada a janela, revalida do zero.
// A janela e curta de proposito: o vinculo que estamos reaproveitando e
// "este numero de WhatsApp provou ser o dono deste contrato", e ele deixa de
// valer se o aparelho trocar de maos.
const IDENT_TTL_MS = 15 * 60 * 1000;
function identidadeFresca(s) {
  if (!s || !s.contrato || !s.verified_at) return false;
  const idade = Date.now() - Number(s.verified_at);
  return idade >= 0 && idade < IDENT_TTL_MS;
}

// "SSID" e jargao: o cliente nao sabe o que e, e normalmente nao lembra como
// a rede dele se chama hoje. Quando o SGP devolve o nome atual, mostramos -
// serve de ancora ("e essa mesma rede") e de exemplo do que responder.
function promptSsid(atualRaw) {
  const atual = String(atualRaw || '').trim();
  if (atual) {
    return 'Sua rede Wi-Fi hoje se chama *' + atual + '*.\n\n' +
           'Qual será o novo nome dela?\n' +
           '_É o nome que aparece na lista de redes Wi-Fi do celular._';
  }
  return 'Qual será o novo nome da sua rede Wi-Fi?\n' +
         '_É o nome que aparece quando você procura redes Wi-Fi no celular._';
}

// Trocar nome E senha juntos era imposicao nossa, nao do SGP: quem so queria
// senha nova era obrigado a rebatizar a rede, e quem so queria renomear tinha
// de inventar uma senha - derrubando a casa inteira por nada.
function promptAlvoWifi(atualRaw) {
  const atual = String(atualRaw || '').trim();
  return (atual ? 'Sua rede Wi-Fi hoje se chama *' + atual + '*.\n\n' : '') +
    'O que você quer alterar?\n\n' +
    '*1* - Só o nome da rede\n' +
    '*2* - Só a senha\n' +
    '*3* - Nome e senha';
}

// O cpemanage escreve o que receber. Mandar novo_ssid vazio quando o cliente
// so queria trocar a senha apagaria o nome da rede dele - por isso o corpo e
// montado aqui, com APENAS os campos que ele pediu para mudar, em vez de
// campos fixos no node de HTTP que sempre viajam (vazios ou nao).
function formWifi(p) {
  const partes = ['contrato=' + encodeURIComponent(p.contrato)];
  if (p.ssid) {
    partes.push('novo_ssid=' + encodeURIComponent(p.ssid));
    partes.push('novo_ssid_5g=' + encodeURIComponent(p.ssid));
  }
  if (p.senha) {
    partes.push('nova_senha=' + encodeURIComponent(p.senha));
    partes.push('nova_senha_5g=' + encodeURIComponent(p.senha));
  }
  return partes.join('&');
}

// Modo genieacs: a NBI indexa por device, nao por contrato, entao e preciso
// achar o equipamento do assinante antes de escrever nele. O SGP entrega dois
// candidatos a chave de juncao no consultacliente: servico_login (o usuario
// PPPoE) e servico_mac.
//
// O caminho do PPPoE no modelo de dados varia entre TR-098 e TR-181 e entre
// indices de WAN, e nao da para saber qual e o certo antes de ver o primeiro
// equipamento real do parque. Por isso a busca vai como $or dos dois padroes
// mais comuns, e GENIEACS_LOGIN_PARAM permite fixar o caminho certo depois
// que ele for descoberto - o que deixa a busca mais barata e menos ambigua.
//
// Ambiguidade aqui e perigosa: escrever Wi-Fi no equipamento errado derruba a
// casa de outra pessoa. Por isso quem consome esta busca ("Montar Tarefa
// Wifi") so aplica quando ela devolve EXATAMENTE um device.
function acsQuery(login, mac) {
  const ors = [];
  const l = String(login || '').trim();
  if (l) {
    const fixo = String($env.GENIEACS_LOGIN_PARAM || '').trim();
    const caminhos = fixo ? [fixo] : [
      'InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.Username',
      'Device.PPP.Interface.1.Username'];
    caminhos.forEach(function (c) {
      const o = {}; o[c + '._value'] = l; ors.push(o);
    });
  }
  // Em boa parte das ONUs o SerialNumber do TR-069 e o MAC (com ou sem ':').
  // E palpite, mas palpite seguro: se casar com um device diferente do que o
  // login casou, a busca devolve dois e nada e aplicado.
  const m = String(mac || '').replace(/[^0-9a-fA-F]/g, '').toUpperCase();
  if (m.length === 12) {
    ors.push({ '_deviceId._SerialNumber': m });
    ors.push({ '_deviceId._SerialNumber': m.match(/.{2}/g).join(':') });
  }
  if (!ors.length) return null;
  return JSON.stringify(ors.length === 1 ? ors[0] : { $or: ors });
}

// No modo 'olt' o nome e a senha terminam numa linha de comando de switch. O
// CLI da ZTE nao aceita espaco no nome, e trata '?' como pedido de ajuda no meio
// da linha - o que quebraria a sessao inteira. O servico intermediario recusa
// esses caracteres; recusar aqui tambem evita o pior desfecho, que e a pessoa
// escolher nome e senha, confirmar, e so entao descobrir que nao valia.
function ssidRecusado(t) {
  if (t.length < 1 || t.length > 32) {
    return 'O nome da rede deve ter entre 1 e 32 caracteres. Envie novamente:';
  }
  if (WIFI_MODO === 'olt') {
    if (/\s/.test(t)) {
      return 'O nome da rede não pode ter espaços. Use hífen ou ponto — por ' +
             'exemplo *Casa-do-Joao*. Envie novamente:';
    }
    if (!/^[A-Za-z0-9._-]+$/.test(t)) {
      return 'O nome da rede só aceita letras, números, ponto, hífen e ' +
             'sublinhado (sem acentos). Envie novamente:';
    }
    return null;
  }
  if (/[\x00-\x1f]/.test(t)) return 'O nome da rede tem caracteres inválidos. Envie novamente:';
  return null;
}

function senhaRecusada(t) {
  if (t.length < 8 || t.length > 63) {
    return 'A senha precisa ter entre 8 e 63 caracteres. Envie novamente:';
  }
  if (WIFI_MODO === 'olt') {
    if (/\s/.test(t)) return 'A senha não pode ter espaços. Envie novamente:';
    if (!/^[A-Za-z0-9!@#$%^&*()_+=,.:;<>[\]{}|~-]+$/.test(t)) {
      return 'A senha tem um caractere que não consigo usar (acento, aspas ou ' +
             'interrogação). Use letras, números e símbolos comuns. Envie novamente:';
    }
    return null;
  }
  if (!/^[\x20-\x7e]+$/.test(t)) {
    return 'A senha só pode ter letras, números e símbolos comuns (sem acentos/emoji). Envie novamente:';
  }
  return null;
}

// Tela de confirmacao: mostra so o que vai mudar. Repetir o nome atual como se
// fosse alteracao faria o cliente achar que a rede vai ser renomeada.
function confirmarWifi(ssid, senha, modo) {
  const linhas = [];
  if (ssid) linhas.push('*Novo nome da rede:* ' + ssid);
  if (senha) linhas.push('*Nova senha:* ' + senha);
  // No modo 'chamado' quem aplica e a equipe, nao o bot. Prometer que os
  // aparelhos vao cair "ao confirmar" seria mentira: nada acontece agora.
  if (modo === 'chamado') {
    let p = 'Confira o pedido antes de eu registrar:\n\n' + linhas.join('\n') + '\n\n';
    p += 'Vou abrir um chamado para nossa equipe aplicar a alteração. ' +
         'Você recebe o número do protocolo aqui.\n\n';
    if (senha) {
      p += '_A senha ficará visível para a equipe técnica, que precisa dela ' +
           'para configurar o equipamento._\n\n';
    }
    return p + 'Digite *1* para confirmar ou *2* para cancelar.';
  }

  let t = 'Confira antes de aplicar:\n\n' + linhas.join('\n') + '\n\n';
  if (senha) {
    t += 'Ao confirmar, *todos os aparelhos conectados* (celulares, TV, ' +
         'câmeras) vão desconectar e precisarão ser reconectados com a ' +
         'senha nova.\n\n';
  } else {
    t += 'Ao confirmar, os aparelhos conectados podem cair por alguns ' +
         'instantes. A senha continua a mesma.\n\n';
  }
  return t + 'Digite *1* para confirmar ou *2* para cancelar.';
}

// Submenu de regularizacao (cliente suspenso por falta de pagamento).
const MENU_REGULARIZAR =
  'Sua internet está com o acesso *bloqueado/reduzido por falta de pagamento*.\n\n' +
  'Como você quer resolver?\n\n' +
  '*1* - Pagar agora (PIX ou boleto)\n' +
  '*2* - Promessa de pagamento\n' +
  '*3* - Falar com um atendente\n\n' +
  '_Assim que o pagamento é identificado, o acesso normaliza automaticamente._';

// Depois que a identidade e confirmada, para onde vai depende do que o
// cliente escolheu no menu. Centralizado aqui para os tres modulos usarem
// exatamente a mesma validacao.
function aposIdentidade(intent, s) {
  // Suspenso (falta de pagamento): prioriza regularizar. So o financeiro segue
  // direto (boleto/PIX); as outras opcoes caem no submenu de regularizar.
  if (s.suspenso && intent !== 'financeiro') {
    return { sgp_action: 'none', next_step: 'regularizar', sgp_payload: {},
             reply_text: MENU_REGULARIZAR };
  }
  if (intent === 'financeiro') {
    return { sgp_action: 'segunda_via', next_step: 'menu', reply_text: null,
             sgp_payload: { contrato: s.contrato } };
  }
  if (intent === 'suporte') {
    return { sgp_action: 'none', next_step: 'awaiting_support_desc', sgp_payload: {},
             reply_text: 'Descreva o problema que você está enfrentando (em uma mensagem):' };
  }
  if (intent === 'diagnostico') {
    return { sgp_action: 'diagnostico', next_step: 'menu', reply_text: null,
             sgp_payload: { contrato: s.contrato, mac: s.mac || '',
                            valor_aberto: s.valor_aberto || 0 } };
  }
  // Sem troca de nome nao ha o que perguntar: pula direto para a senha.
  if (!WIFI_NOME_ON) {
    return { sgp_action: 'none', next_step: 'awaiting_password', sgp_payload: {},
             reply_text: 'Envie a nova senha do Wi-Fi, de 8 a 63 caracteres, sem espaços ou acentos.\n' +
             '_Anote onde conseguir consultar: todo aparelho da casa vai precisar ' +
             'dela para voltar a conectar._' };
  }
  return { sgp_action: 'none', next_step: 'awaiting_wifi_what', sgp_payload: {},
           reply_text: promptAlvoWifi(s.wifi_ssid_atual) };
}

let reply_text = null;
let next_step = step;
let session_patch = {};
let sgp_action = 'none';
let sgp_payload = {};

// "menu" digitado a qualquer momento reinicia o atendimento - inclusive
// estando ja no menu. Parece redundante (a resposta e a mesma), mas nao e:
// desde que a identidade validada sobrevive entre modulos, "sair" precisa
// ser um jeito explicito de encerrar. Sem isso, quem digita "sair" achando
// que fechou o atendimento deixa a sessao autenticada aberta na janela.
if (/^(menu|sair|voltar|inicio|0)$/i.test(text)) {
  return [{ json: { phone, text, step, session,
    reply_text: MENU, next_step: 'menu',
    session_patch: { reset: true }, sgp_action: 'none', sgp_payload: {} } }];
}

// Na fila do atendente (human_handoff) o cliente ainda pode resolver sozinho:
// um numero de menu digitado ali e tratado como se ele estivesse no menu.
let stepEfetivo = step;
if (step === 'human_handoff' && /^[1-5]$/.test(text)) stepEfetivo = 'menu';

switch (stepEfetivo) {
  case 'menu': {
    // Os numeros das outras opcoes nao mudam quando o Wi-Fi sai: cliente
    // costuma responder olhando uma mensagem antiga da conversa, e renumerar
    // faria quem pediu boleto cair no diagnostico.
    if (text === '1' && !WIFI_ON) {
      reply_text = 'A troca de nome e senha do Wi-Fi pelo atendimento ' +
        'automático ainda não está disponível.\n\n' +
        'Digite *5* para falar com um atendente, que faz isso para você.';
      next_step = 'menu';
    } else if ((WIFI_ON ? ['1', '2', '3', '4'] : ['2', '3', '4']).includes(text)) {
      const intents = { '1': 'wifi', '2': 'financeiro', '3': 'suporte', '4': 'diagnostico' };
      const it = intents[text];
      if (identidadeFresca(session)) {
        // Ja provou quem e ha poucos minutos - vai direto ao que pediu.
        const d = aposIdentidade(it, session);
        reply_text = d.reply_text;
        next_step = d.next_step;
        sgp_action = d.sgp_action;
        sgp_payload = d.sgp_payload;
        // ident_reaproveitada vai para a auditoria: se um dia for preciso
        // investigar uma alteracao, tem que dar para saber que ela nao pediu
        // CPF de novo e qual validacao anterior a autorizou.
        session_patch = { attempts: 0, intent: it, ident_reaproveitada: true };
      } else {
        reply_text = 'Para sua segurança, informe o CPF/CNPJ do titular da conta (somente números):';
        next_step = 'awaiting_cpf';
        session_patch = { attempts: 0, intent: it, ident_reaproveitada: undefined };
      }
    } else if (text === '5') {
      reply_text = 'Certo! Você entrou na fila de atendimento. 👍\n\n' +
        'Um atendente vai te responder por aqui em instantes — pode deixar essa ' +
        'conversa aberta.\n\nSe preferir resolver agora, digite *menu* para ver ' +
        'as opções ou o número da opção direto.';
      next_step = 'human_handoff';
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
        reply_text = 'Não consegui validar seu documento. Vou te transferir para um atendente humano.';
        next_step = 'human_handoff';
        session_patch = { attempts: 0 };
      } else {
        reply_text = 'CPF/CNPJ inválido. Digite novamente, apenas números (tentativa ' + n + '/3):';
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
      reply_text = 'Opção inválida. Responda com o número do contrato desejado (1 a ' + opcoes.length + '):';
      next_step = 'awaiting_contract_choice';
    } else {
      const esc = opcoes[idx - 1];
      session_patch = { contrato: esc.contrato, valor_aberto: esc.valor_aberto || 0,
                        suspenso: !!esc.suspenso, contract_options: undefined };
      if (session.second_factor_pending) {
        reply_text = 'Para confirmar sua identidade, informe a data de nascimento do titular (DD/MM/AAAA):';
        next_step = 'awaiting_second_factor';
        session_patch.attempts = 0;
      } else {
        // O contrato escolhido ainda nao esta em session (o upsert vem depois),
        // entao decide sobre a sessao ja com o patch aplicado.
        const s = Object.assign({}, session, session_patch);
        const d = aposIdentidade(session.intent, s);
        reply_text = d.reply_text;
        next_step = d.next_step;
        sgp_action = d.sgp_action;
        sgp_payload = d.sgp_payload;
        session_patch.verified_at = Date.now();
      }
    }
    break;
  }

  case 'awaiting_second_factor': {
    const alvo = normDate(session.second_factor_target);
    const resp = normDate(text);
    if (alvo && resp && alvo === resp) {
      const d = aposIdentidade(session.intent, session);
      reply_text = d.reply_text;
      next_step = d.next_step;
      sgp_action = d.sgp_action;
      sgp_payload = d.sgp_payload;
      session_patch = { attempts: 0, second_factor_target: undefined,
                        second_factor_pending: undefined, verified_at: Date.now() };
    } else {
      const n = attempts + 1;
      if (n >= 3) {
        reply_text = 'Não consegui confirmar sua identidade. Vou te transferir para um atendente humano.';
        next_step = 'human_handoff';
        session_patch = { attempts: 0, second_factor_target: undefined };
      } else {
        reply_text = 'Data não confere. Envie no formato DD/MM/AAAA (tentativa ' + n + '/3):';
        next_step = 'awaiting_second_factor';
        session_patch = { attempts: n };
      }
    }
    break;
  }

  // ---------------- Modulo 1: Wi-Fi ----------------
  case 'awaiting_wifi_what': {
    if (text === '1' || text === '3') {
      session_patch = { wifi_alvo: (text === '1' ? 'nome' : 'ambos') };
      reply_text = promptSsid(session.wifi_ssid_atual);
      next_step = 'awaiting_ssid';
    } else if (text === '2') {
      session_patch = { wifi_alvo: 'senha' };
      reply_text = 'Envie a nova senha do Wi-Fi, de 8 a 63 caracteres, sem acentos.\n' +
        '_Anote onde conseguir consultar: todo aparelho da casa vai precisar ' +
        'dela para voltar a conectar._';
      next_step = 'awaiting_password';
    } else {
      reply_text = 'Não entendi. ' + promptAlvoWifi(session.wifi_ssid_atual);
      next_step = 'awaiting_wifi_what';
    }
    break;
  }

  case 'awaiting_ssid': {
    const recusaSsid = ssidRecusado(text);
    if (recusaSsid) {
      reply_text = recusaSsid;
      next_step = 'awaiting_ssid';
    } else if (session.wifi_alvo === 'nome') {
      // So o nome: nao ha senha a pedir, vai direto para a confirmacao.
      reply_text = confirmarWifi(text, null, WIFI_MODO);
      next_step = 'awaiting_wifi_confirm';
      session_patch = { ssid_new: text };
    } else {
      reply_text = 'Nome definido como "' + text + '".\n\n' +
        'Agora envie a nova senha do Wi-Fi, de 8 a 63 caracteres, sem acentos.\n' +
        '_Anote onde conseguir consultar: todo aparelho da casa vai precisar ' +
        'dela para voltar a conectar._';
      next_step = 'awaiting_password';
      session_patch = { ssid_new: text };
    }
    break;
  }

  case 'awaiting_password': {
    const recusaSenha = senhaRecusada(text);
    if (recusaSenha) {
      reply_text = recusaSenha;
      next_step = 'awaiting_password';
    } else {
      // O cpemanage nao tem chamada de leitura: toda requisicao ESCREVE no
      // roteador. Sem esta confirmacao, uma mensagem enviada por engano ja
      // derruba a casa inteira, e nao ha como desfazer.
      reply_text = confirmarWifi(
        session.wifi_alvo === 'senha' ? null : session.ssid_new, text, WIFI_MODO);
      next_step = 'awaiting_wifi_confirm';
      session_patch = { senha_new: text };
    }
    break;
  }

  case 'awaiting_wifi_confirm': {
    if (text === '1') {
      // So viaja o que o cliente pediu para mudar: campo em branco no
      // cpemanage nao e "manter", e "apagar".
      const alvo = WIFI_NOME_ON ? (session.wifi_alvo || 'ambos') : 'senha';
      const p = { contrato: session.contrato,
                  ssid:  alvo === 'senha' ? null : (session.ssid_new || null),
                  senha: alvo === 'nome'  ? null : (session.senha_new || null) };
      if (WIFI_MODO === 'chamado') {
        // Sem ACS, o pedido vira ocorrencia no SGP para a equipe aplicar. Vai
        // com tudo que o tecnico precisa para nao ter de ligar de volta.
        const det = ['Solicitacao de alteracao de Wi-Fi pelo atendimento automatico.'];
        if (p.ssid)  det.push('Novo nome da rede: ' + p.ssid);
        if (p.senha) det.push('Nova senha: ' + p.senha);
        det.push('Solicitado pelo WhatsApp ' + phone + ', identidade validada.');
        sgp_action = 'abrir_chamado';
        sgp_payload = { contrato: session.contrato, conteudo: det.join('\n') };
      } else if (WIFI_MODO === 'olt') {
        // A OLT indexa por porta fisica, nao por contrato. Quem resolve isso e
        // o proprio SGP, que ja guarda slot/pon/onuid da ONU - por isso este
        // caminho passa por uma consulta antes de aplicar.
        sgp_action = 'definir_wifi_olt';
        sgp_payload = { contrato: session.contrato, mac: session.mac || '',
                        ssid: p.ssid, senha: p.senha };
      } else if (WIFI_MODO === 'auto') {
        // O bot decide por aparelho: ZTE troca pela OLT, Huawei pelo ACS. Mas a
        // marca so e conhecida depois de buscar a ONU no SGP (o serial vem de
        // la), entao 'auto' entra pelo mesmo caminho do modo olt - a busca da
        // ONU - e a decisao acontece em "Montar Troca na OLT", que ja tem o
        // serial em maos. O login vai junto porque o caminho do ACS precisa
        // dele para achar o device.
        sgp_action = 'definir_wifi_olt';
        sgp_payload = { contrato: session.contrato, mac: session.mac || '',
                        login: session.login || '', ssid: p.ssid, senha: p.senha,
                        wifi_auto: true };
      } else if (WIFI_MODO === 'genieacs') {
        // Sem chave de juncao nao ha como identificar o equipamento. Parar
        // aqui e melhor que consultar a NBI sem filtro: uma busca vazia
        // devolve a base inteira, e num parque com um unico device
        // provisionado ela devolveria "exatamente um" - o errado.
        const chave = acsQuery(session.login, session.mac);
        if (!chave) {
          reply_text = 'Não consegui identificar seu equipamento para aplicar a ' +
            'alteração. Vou te transferir para um atendente.';
          next_step = 'human_handoff';
        } else {
          p.acs_query = chave;
          sgp_action = 'definir_wifi_acs';
          sgp_payload = p;
        }
      } else {
        p.form = formWifi(p);
        sgp_action = 'definir_wifi';
        sgp_payload = p;
      }
    } else if (text === '2') {
      reply_text = 'Alteração cancelada. Sua rede continua como estava.\n\n' + MENU;
      next_step = 'menu';
      session_patch = { ssid_new: undefined, senha_new: undefined, wifi_alvo: undefined };
    } else {
      reply_text = 'Digite *1* para confirmar a alteração ou *2* para cancelar.';
      next_step = 'awaiting_wifi_confirm';
    }
    break;
  }

  // ---------------- Modulo 3: Suporte ----------------
  case 'awaiting_support_desc': {
    if (text.length < 10) {
      reply_text = 'Preciso de um pouco mais de detalhe para abrir o chamado (mínimo 10 caracteres). Descreva o problema:';
      next_step = 'awaiting_support_desc';
    } else if (text.length > 1000) {
      reply_text = 'Descrição muito longa. Resuma em até 1000 caracteres:';
      next_step = 'awaiting_support_desc';
    } else {
      sgp_action = 'abrir_chamado';
      sgp_payload = { contrato: session.contrato, conteudo: text };
    }
    break;
  }

  case 'regularizar': {
    // Submenu do cliente suspenso por falta de pagamento.
    if (text === '1') {
      // Pagar: cai no financeiro (2a via com PIX/boleto).
      sgp_action = 'segunda_via'; next_step = 'menu';
      sgp_payload = { contrato: session.contrato };
    } else if (text === '2') {
      // Promessa de pagamento: o bot nao CRIA (a API so lista). Consulta se ja
      // existe uma; se sim informa, se nao encaminha ao atendente.
      sgp_action = 'promessa'; next_step = 'menu';
      sgp_payload = { contrato: session.contrato };
    } else if (text === '3') {
      reply_text = 'Certo! Você entrou na fila de atendimento. Um atendente vai ' +
        'te responder por aqui em instantes.\n\nSe preferir, digite *1* para pagar ' +
        'agora (PIX/boleto).';
      next_step = 'human_handoff';
    } else {
      reply_text = MENU_REGULARIZAR;
      next_step = 'regularizar';
    }
    break;
  }

  case 'human_handoff': {
    // Fila: o bot NAO fica mudo. Segue fazendo companhia ate um atendente
    // assumir de fato (no painel). Enquanto isso, tranquiliza e oferece saida.
    reply_text = '⏳ Você continua na fila — um atendente já vai te responder por ' +
      'aqui, pode aguardar.\n\nSe preferir resolver agora, digite *menu* para ver ' +
      'as opções (ou o número da opção direto).';
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

# ---------------------------------------------------------------- Consulta CPF
JS_PROC_CPF = r"""
const prev = $('Parse & Route').first().json;
const resp = $input.first().json;

let reply_text, next_step;
let sgp_action = 'none';
let sgp_payload = {};
const session_patch = Object.assign({}, prev.session_patch);
const intent = (prev.session_patch && prev.session_patch.intent) || prev.session.intent || 'wifi';

function last8(v) { return String(v || '').replace(/\D/g, '').slice(-8); }

// Mesma decisao usada no Parse & Route: identidade confirmada -> para onde vai.
// Mesmo texto do Parse & Route: cada Code node tem escopo proprio, entao a
// funcao precisa existir nos dois lugares.
function promptSsid(atualRaw) {
  const atual = String(atualRaw || '').trim();
  if (atual) {
    return 'Sua rede Wi-Fi hoje se chama *' + atual + '*.\n\n' +
           'Qual será o novo nome dela?\n' +
           '_É o nome que aparece na lista de redes Wi-Fi do celular._';
  }
  return 'Qual será o novo nome da sua rede Wi-Fi?\n' +
         '_É o nome que aparece quando você procura redes Wi-Fi no celular._';
}

// Trocar nome E senha juntos era imposicao nossa, nao do SGP: quem so queria
// senha nova era obrigado a rebatizar a rede, e quem so queria renomear tinha
// de inventar uma senha - derrubando a casa inteira por nada.
function promptAlvoWifi(atualRaw) {
  const atual = String(atualRaw || '').trim();
  return (atual ? 'Sua rede Wi-Fi hoje se chama *' + atual + '*.\n\n' : '') +
    'O que você quer alterar?\n\n' +
    '*1* - Só o nome da rede\n' +
    '*2* - Só a senha\n' +
    '*3* - Nome e senha';
}

// O cpemanage escreve o que receber. Mandar novo_ssid vazio quando o cliente
// so queria trocar a senha apagaria o nome da rede dele - por isso o corpo e
// montado aqui, com APENAS os campos que ele pediu para mudar, em vez de
// campos fixos no node de HTTP que sempre viajam (vazios ou nao).
function formWifi(p) {
  const partes = ['contrato=' + encodeURIComponent(p.contrato)];
  if (p.ssid) {
    partes.push('novo_ssid=' + encodeURIComponent(p.ssid));
    partes.push('novo_ssid_5g=' + encodeURIComponent(p.ssid));
  }
  if (p.senha) {
    partes.push('nova_senha=' + encodeURIComponent(p.senha));
    partes.push('nova_senha_5g=' + encodeURIComponent(p.senha));
  }
  return partes.join('&');
}

// Tela de confirmacao: mostra so o que vai mudar. Repetir o nome atual como se
// fosse alteracao faria o cliente achar que a rede vai ser renomeada.
function confirmarWifi(ssid, senha, modo) {
  const linhas = [];
  if (ssid) linhas.push('*Novo nome da rede:* ' + ssid);
  if (senha) linhas.push('*Nova senha:* ' + senha);
  // No modo 'chamado' quem aplica e a equipe, nao o bot. Prometer que os
  // aparelhos vao cair "ao confirmar" seria mentira: nada acontece agora.
  if (modo === 'chamado') {
    let p = 'Confira o pedido antes de eu registrar:\n\n' + linhas.join('\n') + '\n\n';
    p += 'Vou abrir um chamado para nossa equipe aplicar a alteração. ' +
         'Você recebe o número do protocolo aqui.\n\n';
    if (senha) {
      p += '_A senha ficará visível para a equipe técnica, que precisa dela ' +
           'para configurar o equipamento._\n\n';
    }
    return p + 'Digite *1* para confirmar ou *2* para cancelar.';
  }

  let t = 'Confira antes de aplicar:\n\n' + linhas.join('\n') + '\n\n';
  if (senha) {
    t += 'Ao confirmar, *todos os aparelhos conectados* (celulares, TV, ' +
         'câmeras) vão desconectar e precisarão ser reconectados com a ' +
         'senha nova.\n\n';
  } else {
    t += 'Ao confirmar, os aparelhos conectados podem cair por alguns ' +
         'instantes. A senha continua a mesma.\n\n';
  }
  return t + 'Digite *1* para confirmar ou *2* para cancelar.';
}

// Este node roda separado do Parse & Route e nao enxerga as constantes de la.
const WIFI_NOME_ON = String($env.WIFI_PERMITE_NOME || 'true').trim().toLowerCase() !== 'false';

// Submenu de regularizacao, reaproveitado onde o cliente esta suspenso.
const MENU_REGULARIZAR =
  'Sua internet está com o acesso *bloqueado/reduzido por falta de pagamento*.\n\n' +
  'Como você quer resolver?\n\n' +
  '*1* - Pagar agora (PIX ou boleto)\n' +
  '*2* - Promessa de pagamento\n' +
  '*3* - Falar com um atendente\n\n' +
  '_Assim que o pagamento é identificado, o acesso normaliza automaticamente._';

function aposIdentidade(it, contrato, mac, ssidAtual, valorAberto, suspenso) {
  // Suspenso (falta de pagamento): a prioridade e regularizar. So o financeiro
  // segue direto (boleto/PIX); qualquer outra opcao cai no submenu de regularizar.
  if (suspenso && it !== 'financeiro') {
    return { sgp_action: 'none', next_step: 'regularizar', sgp_payload: {},
             reply_text: MENU_REGULARIZAR };
  }
  if (it === 'financeiro') {
    return { sgp_action: 'segunda_via', next_step: 'menu', reply_text: null,
             sgp_payload: { contrato: contrato } };
  }
  if (it === 'diagnostico') {
    return { sgp_action: 'diagnostico', next_step: 'menu', reply_text: null,
             sgp_payload: { contrato: contrato, mac: mac || '',
                            valor_aberto: valorAberto || 0 } };
  }
  if (it === 'suporte') {
    return { sgp_action: 'none', next_step: 'awaiting_support_desc', sgp_payload: {},
             reply_text: 'Descreva o problema que você está enfrentando (em uma mensagem):' };
  }
  if (!WIFI_NOME_ON) {
    return { sgp_action: 'none', next_step: 'awaiting_password', sgp_payload: {},
             reply_text: 'Envie a nova senha do Wi-Fi, de 8 a 63 caracteres, sem espaços ou acentos.\n' +
             '_Anote onde conseguir consultar: todo aparelho da casa vai precisar ' +
             'dela para voltar a conectar._' };
  }
  return { sgp_action: 'none', next_step: 'awaiting_wifi_what', sgp_payload: {},
           reply_text: promptAlvoWifi(ssidAtual) };
}

// Resposta do SGP: { msg, contratos: [ ... ] }
const contratos = Array.isArray(resp && resp.contratos) ? resp.contratos : [];
// contratoStatus (TSMX): 1=Ativo. Falta de pagamento tem MAIS de um codigo:
// 4=Suspenso e 7="Ativo V. Reduzida" (throttle, ainda online) - ambos com valor
// em aberto. ATENDIVEIS = Ativo + os de atraso: o cliente precisa se identificar
// e PAGAR. Barrar aqui era o "diz que nao esta ativo sendo que esta". Ativo pleno
// vem antes dos em atraso quando ha mais de um contrato.
const STATUS_ATRASO = [4, 7];
function ehAtraso(c) { return STATUS_ATRASO.indexOf(c.contratoStatus) >= 0; }
const ativos = contratos
  .filter(function (c) { return c.contratoStatus === 1 || ehAtraso(c); })
  .sort(function (a, b) { return (a.contratoStatus === 1 ? 0 : 1) - (b.contratoStatus === 1 ? 0 : 1); });

if (contratos.length === 0) {
  reply_text = 'Não encontrei nenhum contrato com esse CPF/CNPJ. Confira o número ou digite *5* para falar com um atendente.';
  next_step = 'menu';
} else if (ativos.length === 0) {
  // Nem ativo nem suspenso: so cancelado/inativo. Nao ha self-service - atendente.
  reply_text = 'Localizei seu cadastro, mas não há contrato ativo no momento. Vou te transferir para um atendente.';
  next_step = 'human_handoff';
} else {
  const ref = ativos[0];
  const refSuspenso = ehAtraso(ref);

  // ---- Segundo fator: o numero do WhatsApp bate com algum telefone do cadastro? ----
  const telefones = [];
  ativos.forEach(function (c) {
    (c.telefones || []).forEach(function (t) { if (t && t.contato) telefones.push(t.contato); });
  });
  const telefoneBate = telefones.some(function (t) { return last8(t) && last8(t) === last8(prev.phone); });

  session_patch.nome = ref.razaoSocial || '';
  // servico_mac casa com o phy_addr da ONU - e o plano B para achar o
  // equipamento quando o filtro por contrato nao retorna nada.
  session_patch.mac = ref.servico_mac || ref.servico_mac2 || '';
  // Valor em aberto do contrato: alimenta o aviso de "corte por falta de
  // pagamento" no diagnostico, quando a base mantem o contrato Ativo e bloqueia
  // no RADIUS/OLT (a suspensao formal, status 4, e tratada la em cima).
  session_patch.valor_aberto = parseFloat(ref.contratoValorAberto) || 0;
  // Suspenso por falta de pagamento: o fluxo prioriza regularizar (ver boleto/PIX).
  session_patch.suspenso = refSuspenso;
  // Usuario PPPoE: chave de juncao preferida com o GenieACS no modo
  // 'genieacs', porque e o unico campo que o SGP e o equipamento enxergam
  // com o mesmo valor. O MAC entra so como segundo candidato.
  session_patch.login = ref.servico_login || '';
  // Nome atual da rede: usado para o cliente reconhecer de qual Wi-Fi
  // estamos falando. Pode vir vazio - a base nem sempre tem esse campo.
  session_patch.wifi_ssid_atual = ref.servico_wifi_ssid || '';

  if (ativos.length === 1) {
    session_patch.contrato = ref.contratoId;
  } else {
    // Cliente com mais de um contrato ativo: precisa escolher qual
    session_patch.contract_options = ativos.slice(0, 9).map(function (c) {
      return { contrato: c.contratoId,
               valor_aberto: parseFloat(c.contratoValorAberto) || 0,
               suspenso: ehAtraso(c),
               label: (c.servico_plano || c.planointernet || 'Plano') + ' - ' +
                      (c.endereco_logradouro || '') + ' ' + (c.endereco_numero || '') +
                      (ehAtraso(c) ? ' (em atraso)' : '') };
    });
  }

  const listaContratos = function () {
    return 'Você tem mais de um contrato ativo. Qual deles?\n\n' +
      session_patch.contract_options.map(function (o, i) { return '*' + (i + 1) + '* - ' + o.label; }).join('\n');
  };

  if (telefoneBate) {
    if (ativos.length === 1) {
      // Identidade confirmada pelo proprio numero: marca a janela em que os
      // outros modulos podem ser usados sem repetir CPF.
      session_patch.verified_at = Date.now();
      const d = aposIdentidade(intent, ref.contratoId, session_patch.mac,
                               session_patch.wifi_ssid_atual, session_patch.valor_aberto,
                               refSuspenso);
      reply_text = d.reply_text;
      next_step = d.next_step;
      sgp_action = d.sgp_action;
      sgp_payload = d.sgp_payload;
      if (d.reply_text && intent === 'wifi') {
        reply_text = 'Confirmado' + (ref.razaoSocial ? ', ' + ref.razaoSocial : '') + '! ' + d.reply_text;
      }
    } else {
      reply_text = listaContratos();
      next_step = 'awaiting_contract_choice';
    }
  } else {
    // Numero nao cadastrado -> exige data de nascimento
    const nasc = ref.dataNascimento || '';
    if (!nasc) {
      reply_text = 'Não consegui confirmar sua identidade automaticamente. Vou te transferir para um atendente.';
      next_step = 'human_handoff';
    } else if (ativos.length === 1) {
      session_patch.second_factor_target = nasc;
      session_patch.attempts = 0;
      reply_text = 'Esse número não é o cadastrado no contrato. Para confirmar que é você, informe a data de nascimento do titular (DD/MM/AAAA):';
      next_step = 'awaiting_second_factor';
    } else {
      session_patch.second_factor_target = nasc;
      session_patch.second_factor_pending = true;
      reply_text = listaContratos();
      next_step = 'awaiting_contract_choice';
    }
  }
}

return [{ json: Object.assign({}, prev, {
  reply_text: reply_text, next_step: next_step, session_patch: session_patch,
  sgp_action: sgp_action, sgp_payload: sgp_payload,
}) }];
"""

# ---------------------------------------------------------------- Wi-Fi
JS_PROC_WIFI = r"""
const prev = $('Parse & Route').first().json;
const resp = $input.first().json;

let reply_text, next_step;
const session_patch = Object.assign({}, prev.session_patch);

// A senha em claro so existe na sessao entre a tela de confirmacao e esta
// chamada. Chegou aqui, sai - deu certo ou nao. Sem isso ela ficaria no
// JSONB de wa_sessions ate a limpeza de 30 min.
session_patch.senha_new = undefined;
session_patch.ssid_new = undefined;
session_patch.wifi_alvo = undefined;

// A via (ACS ou OLT) NAO pode sair do sgp_action do Parse & Route: no modo auto
// a decisao acontece depois, em "Montar Troca na OLT" (ZTE->olt, Huawei->acs),
// e aquele valor fica defasado - tratar uma resposta do ACS como se fosse da
// OLT faz o 202 (tarefa enfileirada, normal atras de CGNAT) virar "falhou" e o
// cliente ir para o atendente sem motivo. A via real e dita por qual node de
// montagem rodou e o que ele produziu.
let acs = false, olt = false;
try { acs = !!(($('Montar Tarefa Wifi').first().json) || {}).acs_device_id; } catch (e) { acs = false; }
if (!acs) {
  try { olt = (($('Montar Troca na OLT').first().json) || {}).wifi_rota === 'olt'; } catch (e) { olt = false; }
}
if (!acs && !olt) {          // modos explicitos, sem os nodes de montagem do auto
  acs = prev.sgp_action === 'definir_wifi_acs';
  olt = prev.sgp_action === 'definir_wifi_olt';
}
const ssidNovo = prev.sgp_payload && prev.sgp_payload.ssid;
const senhaNova = prev.sgp_payload && prev.sgp_payload.senha;

// Duas origens, duas formas de dizer "deu certo":
//   cpemanage do SGP -> { msg, success }
//   NBI do GenieACS  -> 200 quando o roteador aplicou na hora, 202 quando ele
//                       nao atendeu o connection request e a tarefa ficou na
//                       fila. 202 nao e erro, mas tambem nao e "pronto".
let sucesso = false, enfileirado = false, auditoria;
if (acs) {
  const status = Number(resp && resp.statusCode);
  const corpo = (resp && resp.body) || {};
  const fault = (corpo && corpo.fault) ? corpo.fault : null;
  sucesso = status === 200 && !fault;
  enfileirado = status === 202 && !fault;
  // O corpo que a NBI devolve numa falha ECOA a tarefa - e a tarefa carrega a
  // senha em claro. O log de auditoria e consultado pela equipe toda, entao
  // daqui sai so o que serve para diagnosticar.
  // acs_device_id e acs_redes nascem em "Montar Tarefa Wifi", nao no
  // Parse & Route. Este node tambem atende o caminho do cpemanage, onde
  // aquele node nao roda - e referenciar node que nao executou levanta
  // excecao no n8n, entao a leitura vai protegida.
  let montado = {};
  try { montado = $('Montar Tarefa Wifi').first().json || {}; } catch (e) { montado = {}; }
  auditoria = { via: 'genieacs', device: montado.acs_device_id || null,
                redes: montado.acs_redes || null, status: status || null,
                fault: fault ? (fault.detail || fault) : null };
} else if (olt) {
  // O servico intermediario responde { ok, detalhe }. O detalhe pode conter a
  // linha de erro que a OLT devolveu - que nunca inclui a senha, porque quem
  // monta o comando e o servico e ele nao ecoa o que executou.
  const corpo = (resp && resp.body) || {};
  sucesso = corpo.ok === true;
  let ondeAplicou = null;
  try { ondeAplicou = ($('Montar Troca na OLT').first().json || {}).olt_onu || null; } catch (e) { ondeAplicou = null; }
  auditoria = { via: 'olt', onu: ondeAplicou, detalhe: corpo.detalhe || null,
                status: Number(resp && resp.statusCode) || null };
} else {
  sucesso = !!(resp && resp.success === true);
  auditoria = resp;
}

if (sucesso) {
  // Confirma exatamente o que mudou. Dizer "nome e senha atualizados" para
  // quem so trocou a senha faz o cliente procurar uma rede que nao existe.
  reply_text = 'Pronto! Sua rede Wi-Fi foi atualizada:\n';
  if (ssidNovo) reply_text += '\n*Nome:* ' + ssidNovo;
  if (senhaNova) reply_text += '\n*Senha:* alterada';
  // Pelo cpemanage nao da para saber se a ONU aplicou: o SGP responde success
  // assim que aceita o pedido. Pela NBI, 200 e o roteador confirmando - e ai
  // prometer "alguns minutos" seria inventar uma espera que nao existe.
  reply_text += (acs || olt)
    ? '\n\nA alteração já está valendo. '
    : '\n\nO roteador pode levar alguns minutos para aplicar. ';
  reply_text += senhaNova
    ? 'Seus aparelhos vão precisar conectar de novo com a nova senha.'
    : 'Seus aparelhos devem reconectar sozinhos com a senha de sempre.';
  reply_text += '\n\nDigite *menu* se precisar de mais alguma coisa.';
  next_step = 'menu';
  session_patch.reset = true;
} else if (enfileirado) {
  // A tarefa ficou na fila do ACS e roda quando o equipamento se comunicar de
  // novo. Nao da para cancelar do lado do cliente, entao o minimo e nao deixar
  // a queda dos aparelhos chegar de surpresa daqui a algumas horas.
  reply_text = 'Seu roteador não respondeu agora — deve estar desligado ou ' +
    'sem conexão.\n\nDeixei a alteração agendada: ela vai ser aplicada sozinha ' +
    'assim que o equipamento voltar a se comunicar';
  reply_text += senhaNova
    ? ', e nesse momento os aparelhos da casa vão pedir a senha nova.'
    : '.';
  reply_text += '\n\nSe preferir que alguém acompanhe, digite *5*.';
  next_step = 'menu';
  session_patch.reset = true;
} else {
  const msg = (resp && resp.msg) ? String(resp.msg) : '';
  if (/Gerenciador de CPE/i.test(msg)) {
    reply_text = 'Seu roteador não está habilitado para configuração remota. Vou te transferir para um atendente resolver isso.';
    next_step = 'human_handoff';
  } else if (acs || olt) {
    // Falha na NBI e sempre algo que a equipe precisa olhar (parametro que o
    // modelo recusou, ACS fora do ar). Mandar "tente de novo" so faria o
    // cliente repetir a mesma falha.
    reply_text = 'Não consegui aplicar a alteração no seu equipamento. ' +
      'Sua rede continua como estava. Vou te transferir para um atendente.';
    next_step = 'human_handoff';
  } else {
    reply_text = 'Não consegui aplicar a alteração agora. Tente novamente em alguns minutos ou digite *5* para falar com um atendente.';
    next_step = 'menu';
  }
}

return [{ json: Object.assign({}, prev, {
  reply_text: reply_text,
  next_step: next_step,
  session_patch: session_patch,
  _audit: {
    tipo: 'wifi',
    phone: prev.phone,
    cpf: prev.session.cpf,
    contrato: prev.sgp_payload.contrato,
    ssid_novo: prev.sgp_payload.ssid,
    sucesso: sucesso,
    enfileirado: enfileirado || undefined,
    resposta_sgp: auditoria,
  },
}) }];
"""

# Modo genieacs: traduz "trocar o Wi-Fi do contrato X" para "escrever estes
# parametros neste device". E aqui que mora a complexidade que o cpemanage do
# SGP absorvia por nos - o preco de falar direto com a NBI.
JS_MONTAR_TAREFA_ACS = r"""
const prev = $('Parse & Route').first().json;
const resp = $input.first().json;

const devices = Array.isArray(resp && resp.body) ? resp.body : [];
const ssid  = (prev.sgp_payload && prev.sgp_payload.ssid)  || null;
const senha = (prev.sgp_payload && prev.sgp_payload.senha) || null;

// Toda rede que o bot tocar sai em WPA2-AES puro. O padrao de fabrica de muita
// ONU e WPA/WPA2 misto com TKIP, que o iPhone marca como "Seguranca Fraca" - e
// TKIP e mesmo fraco. Forcar aqui evita deixar o cliente pior do que estava.
// WIFI_FORCA_WPA2=false desliga, para o caso de algum modelo nao aceitar.
const FORCA_WPA2 = String($env.WIFI_FORCA_WPA2 || 'true').trim().toLowerCase() !== 'false';

function falha(motivo, extra) {
  return [{ json: Object.assign({}, prev, {
    acs_device_id: null, acs_task: null,
    acs_falha: motivo, acs_falha_extra: extra || null,
  }) }];
}

// A NBI devolve um array. Zero: o equipamento nao esta provisionado no ACS
// (nem toda ONU esta, e nunca vai estar - ver README do genieacs). Mais de um:
// as chaves de juncao apontaram para equipamentos diferentes, e escolher um
// seria escrever na casa de outra pessoa. Nos dois casos, nao aplica.
// Modo auto: a Huawei so entra no ACS depois que a equipe cria a WAN de
// TR-069 no aparelho. Enquanto isso, o pedido do cliente nao se perde: vira uma
// OS que ja pede a habilitacao E leva a senha escolhida, para o tecnico fazer as
// duas coisas de uma vez. Da proxima vez, o cliente ja e automatico.
if (devices.length === 0 && prev.sgp_payload && prev.sgp_payload.wifi_auto) {
  const det = ['Solicitacao de troca de Wi-Fi pelo atendimento automatico.',
    'O equipamento (Huawei) ainda NAO tem gerencia remota (TR-069) habilitada, ' +
    'por isso a troca nao pode ser feita a distancia.',
    'ACAO: habilitar o TR-069 no aparelho (criar a WAN de servico ' +
    'TR069_INTERNET) e aplicar a senha abaixo.'];
  if (ssid)  det.push('Novo nome da rede: ' + ssid);
  if (senha) det.push('Nova senha: ' + senha);
  det.push('Solicitado pelo WhatsApp ' + prev.phone + ', identidade validada.');
  return [{ json: Object.assign({}, prev, {
    acs_device_id: null, acs_task: null, acs_falha: 'huawei_sem_tr069',
    wifi_rota_acs: 'chamado',
    sgp_action: 'abrir_chamado',
    sgp_payload: { contrato: prev.sgp_payload.contrato, conteudo: det.join('\n') },
    wifi_virou_chamado: true,
    wifi_motivo: 'equipamento sem gerencia remota (TR-069)',
  }) }];
}

if (devices.length === 0) return falha('device_nao_encontrado');
if (devices.length > 1) return falha('device_ambiguo', devices.length);

const dev = devices[0];

// Na NBI cada parametro e um objeto {_value, _type, _writable, _timestamp} e
// as instancias sao chaves numericas.
function val(no) { return (no && typeof no === 'object') ? no._value : undefined; }
function ligado(v) { return v === true || v === 1 || /^(1|true)$/i.test(String(v == null ? '' : v)); }
function escrivel(no) { return !!no && typeof no === 'object' && no._writable !== false; }

const lan = ((dev.InternetGatewayDevice || {}).LANDevice || {})['1'] || {};
const wlan = lan.WLANConfiguration || {};
const indices = Object.keys(wlan).filter(function (k) { return /^\d+$/.test(k); })
  .map(Number).sort(function (a, b) { return a - b; });

// Sem WLANConfiguration no modelo de dados o equipamento pode ser TR-181
// (Device.WiFi.*, mapeamento diferente e nao suportado aqui) ou simplesmente
// nunca ter tido a arvore lida pelo ACS. Em nenhum dos dois casos da para
// escrever as cegas.
if (!indices.length) return falha('sem_wlanconfiguration');

// 2.4 vs 5 GHz. Tres indicios, do mais forte ao mais fraco:
//   1) OperatingFrequencyBand - o campo canonico, mas nem todo firmware expoe.
//   2) Standard - a Huawei manda "11ac"/"11ax" (5 GHz) e "11bgn"/"11n" (2.4),
//      tudo grudado, sem virgula. 'b' e 'g' so existem em 2.4; 'ac'/'ax' so em
//      5. ('n' e dual-band, nao decide sozinho.)
//   3) Convencao de indice, provada nesta rede: 1-4 = 2.4 GHz, 5-8 = 5 GHz.
//      E o ultimo recurso quando o Standard nao decide.
// banda() nunca devolve '' - sempre classifica, para nunca deixar uma rede de
// 5 GHz de fora por falta de rotulo (seria o "mudou e nao mudou").
function banda(inst, idx) {
  const f = val(inst.OperatingFrequencyBand);
  if (f) return /5/.test(String(f)) ? '5' : '2.4';
  const st = String(val(inst.Standard) || '').toLowerCase();
  if (/a[cx]/.test(st)) return '5';                 // 11ac, 11ax
  if (/(^|[,\s])a([,\s]|$)/.test(st)) return '5';    // 802.11a isolado
  if (/[bg]/.test(st)) return '2.4';                // b/g so existem em 2.4
  return Number(idx) >= 5 ? '5' : '2.4';            // convencao de indice
}

// So redes LIGADAS entram: rede desligada e quase sempre a de visitantes que
// o assinante nunca usou, e renomea-la nao ajuda ninguem.
const ligadas = indices.map(function (n) { return { n: n, inst: wlan[String(n)] || {} }; })
  .filter(function (a) { return ligado(val(a.inst.Enable)); });
if (!ligadas.length) return falha('sem_rede_ligada');

// Quando o firmware informa a banda, muda a PRIMEIRA rede de cada banda - que
// e a rede principal. Quando nao informa, nao da para distinguir a principal
// da de visitantes, e a escolha conservadora e mudar todas as ligadas: e o
// mesmo efeito de mandar novo_ssid + novo_ssid_5g pelo cpemanage.
// Com o recurso de indice, banda() sempre classifica; temBanda so continua
// existindo para o caso (teorico) de nao haver rede ligada nenhuma.
const temBanda = ligadas.some(function (a) { return banda(a.inst, a.n) !== ''; });
let alvos;
if (temBanda) {
  const vistas = {};
  alvos = ligadas.filter(function (a) {
    const b = banda(a.inst, a.n) || 'indefinida';
    if (vistas[b]) return false;
    vistas[b] = true;
    return true;
  });
} else {
  alvos = ligadas;
}

// Cada rede escolhida precisa aceitar TUDO que o cliente pediu. Aplicar so no
// que der deixaria o assinante com 2.4 GHz numa senha e 5 GHz noutra - pior
// que nao aplicar, porque ele acha que funcionou e liga para o suporte com um
// sintoma dificil de diagnosticar. Se algum modelo cair aqui, o mapeamento
// dele precisa ser resolvido antes de ser liberado, nao contornado em silencio.
const parametros = [];
for (let i = 0; i < alvos.length; i++) {
  const a = alvos[i];
  const c = 'InternetGatewayDevice.LANDevice.1.WLANConfiguration.' + a.n;
  if (ssid) {
    if (!escrivel(a.inst.SSID)) return falha('ssid_nao_escrivel', a.n);
    parametros.push([c + '.SSID', ssid, 'xsd:string']);
  }
  if (senha) {
    // A senha do Wi-Fi mora em ate tres lugares no modelo de dados, e escrever
    // um que o CPE recusa derruba a tarefa INTEIRA (o setParameterValues e
    // atomico). Medido numa Huawei HG8145V5 em campo (11/09/2026):
    //   - WLANConfiguration.N.KeyPassphrase           -> recusado (fault 9002)
    //   - WLANConfiguration.N.PreSharedKey.1.KeyPassphrase -> ACEITO
    // Ou seja, escrever "todos que existem" nao serve: o KeyPassphrase de cima
    // aparece como escrivel no modelo e mesmo assim derruba tudo.
    //
    // A regra passa a ser: quando ha PreSharedKey.1, a senha vai SO por ele
    // (KeyPassphrase e/ou PreSharedKey - os que existirem). O KeyPassphrase do
    // topo so entra quando nao ha PreSharedKey nenhum, que e o caso de firmwares
    // mais antigos que so tem aquele campo.
    const psk = (a.inst.PreSharedKey || {})['1'] || {};
    const kpTopo = escrivel(a.inst.KeyPassphrase);
    const kpPsk = escrivel(psk.KeyPassphrase);
    const pskPsk = escrivel(psk.PreSharedKey);
    const temPsk = kpPsk || pskPsk;

    if (temPsk) {
      if (kpPsk) parametros.push([c + '.PreSharedKey.1.KeyPassphrase', senha, 'xsd:string']);
      if (pskPsk) parametros.push([c + '.PreSharedKey.1.PreSharedKey', senha, 'xsd:string']);
    } else if (kpTopo) {
      parametros.push([c + '.KeyPassphrase', senha, 'xsd:string']);
    } else {
      return falha('senha_nao_escrivel', a.n);
    }
  }
  // WPA2-AES puro: 11i (nao "WPAand11i" misto) e AES (nao TKIP). So os campos
  // que o modelo deixa escrever, para nao derrubar a tarefa inteira num firmware
  // que nomeie diferente - mesma disciplina da senha.
  if (FORCA_WPA2) {
    if (escrivel(a.inst.BeaconType))
      parametros.push([c + '.BeaconType', '11i', 'xsd:string']);
    if (escrivel(a.inst.IEEE11iEncryptionModes))
      parametros.push([c + '.IEEE11iEncryptionModes', 'AESEncryption', 'xsd:string']);
    if (escrivel(a.inst.IEEE11iAuthenticationMode))
      parametros.push([c + '.IEEE11iAuthenticationMode', 'PSKAuthentication', 'xsd:string']);
  }
}
if (!parametros.length) return falha('nada_a_escrever');

return [{ json: Object.assign({}, prev, {
  acs_device_id: dev._id,
  acs_task: { name: 'setParameterValues', parameterValues: parametros },
  acs_redes: alvos.map(function (a) { return a.n; }),
  acs_falha: null,
  wifi_rota_acs: 'aplicar',
}) }];
"""

# Nao aplicou porque nem chegamos a tentar: device ausente, ambiguo ou com um
# modelo de dados que nao sabemos escrever. Do lado do cliente e tudo a mesma
# coisa - ninguem mexeu no roteador dele - mas o motivo tem de ficar no log,
# porque e ele que diz o que corrigir no provisionamento.
JS_ACS_NAO_APLICOU = r"""
const prev = $input.first().json;

const session_patch = Object.assign({}, prev.session_patch);
session_patch.senha_new = undefined;
session_patch.ssid_new = undefined;
session_patch.wifi_alvo = undefined;

return [{ json: Object.assign({}, prev, {
  reply_text: 'Não consegui aplicar a alteração no seu equipamento agora. ' +
    'Sua rede continua como estava. Vou te transferir para um atendente, ' +
    'que resolve isso para você.',
  next_step: 'human_handoff',
  session_patch: session_patch,
  _audit: {
    tipo: 'wifi',
    phone: prev.phone,
    cpf: prev.session.cpf,
    contrato: prev.sgp_payload.contrato,
    ssid_novo: prev.sgp_payload.ssid,
    sucesso: false,
    // Sem senha e sem a tarefa: o log de auditoria e consultado pela equipe
    // toda, o dado que importa aqui e o motivo.
    resposta_sgp: { via: 'genieacs', falha: prev.acs_falha,
                    detalhe: prev.acs_falha_extra || null },
  },
}) }];
"""


# Modo olt: traduz "trocar o Wi-Fi do contrato X" para "esta ONU, nesta porta da
# OLT". O SGP e quem sabe onde o assinante esta fisicamente - slot, pon e onuid
# vem do /api/fttx/onu/list/, e a partir deles se monta o endereco que a OLT
# entende. Nenhum cadastro novo e necessario.
JS_MONTAR_OLT = r"""
const prev = $('Parse & Route').first().json;

function falha(motivo, extra) {
  return [{ json: Object.assign({}, prev, {
    olt_onu: null, olt_falha: motivo, olt_falha_extra: extra || null,
    wifi_rota: 'chamado',
  }) }];
}

// Copia local de acsQuery + a busca por serial da ONU, que este node tem em
// maos (phy_addr do SGP) e o Parse & Route nao tinha. So usada no modo auto.
//
// O serial e a chave MAIS confiavel para Huawei: nao depende de o GenieACS ter
// lido a arvore WAN (o login PPPoE mora la, e pode nao ter sido lido ainda -
// medido em campo, uma ONU registrada nao era achada por login). O serial GPON
// do SGP vem como "HWTC1FC5E5AB": os 4 primeiros caracteres sao o vendor ID em
// ASCII, que no _SerialNumber do TR-069 aparecem em HEX ("48575443"), seguidos
// do resto igual. Ex.: HWTC1FC5E5AB -> 485754431FC5E5AB.
function serialTr069(phy) {
  const s = String(phy || '').trim().toUpperCase();
  if (!/^[A-Z]{4}[0-9A-F]{8}$/.test(s)) return null;
  let hex = '';
  for (let i = 0; i < 4; i++) hex += ('0' + s.charCodeAt(i).toString(16)).slice(-2);
  return (hex + s.slice(4)).toUpperCase();
}
function montarAcsQuery(login, mac, phy) {
  const ors = [];
  const l = String(login || '').trim();
  if (l) {
    const fixo = String($env.GENIEACS_LOGIN_PARAM || '').trim();
    const caminhos = fixo ? [fixo] : [
      'InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.Username',
      'Device.PPP.Interface.1.Username'];
    caminhos.forEach(function (c) { const o = {}; o[c + '._value'] = l; ors.push(o); });
  }
  const m = String(mac || '').replace(/[^0-9a-fA-F]/g, '').toUpperCase();
  if (m.length === 12) {
    ors.push({ '_deviceId._SerialNumber': m });
    ors.push({ '_deviceId._SerialNumber': m.match(/.{2}/g).join(':') });
  }
  const sn = serialTr069(phy);
  if (sn) {
    ors.push({ '_deviceId._SerialNumber': sn });
    // Algumas ONUs registram o proprio phy_addr sem converter o prefixo.
    ors.push({ '_deviceId._SerialNumber': String(phy).trim().toUpperCase() });
  }
  if (!ors.length) return null;
  return JSON.stringify(ors.length === 1 ? ors[0] : { $or: ors });
}

// O n8n quebra resposta JSON que e array em VARIOS itens, um por elemento.
// Entao $input.first().json pode ser o array inteiro ou apenas a primeira ONU
// dele, conforme a versao e a configuracao do node. Ler so o primeiro item e
// testar Array.isArray da falso negativo: a lista vira vazia e o bot responde
// "nao localizei seu equipamento" com a ONU bem ali, vinculada ao contrato.
// Aconteceu em producao em 31/08/2026. Aceita as duas formas.
const itens = $input.all().map(function (i) { return i.json; });
let onus = [];
if (itens.length === 1 && Array.isArray(itens[0])) {
  onus = itens[0];
} else {
  onus = itens.filter(function (o) {
    return o && typeof o === 'object' && (o.slot !== undefined || o.id !== undefined);
  });
}
const mac = String((prev.sgp_payload && prev.sgp_payload.mac) || '')
  .replace(/[^a-zA-Z0-9]/g, '').toLowerCase();

// Uma ONU: e ela. Varias: so segue se o MAC do contrato desempatar.
// Escolher "a primeira" aqui seria escrever no equipamento de outra pessoa - o
// modulo de diagnostico pode fazer isso porque so le; este escreve.
let escolhida = null;
if (onus.length === 1) {
  escolhida = onus[0];
} else if (onus.length > 1 && mac) {
  escolhida = onus.find(function (o) {
    return String(o.phy_addr || '').replace(/[^a-zA-Z0-9]/g, '').toLowerCase() === mac;
  }) || null;
}

if (!onus.length) return falha('onu_nao_encontrada');
if (!escolhida) return falha('onu_ambigua', onus.length);

// No modo auto, a MARCA decide o caminho, e o serial GPON diz a marca:
//   HWTC = Huawei -> so o TR-069 (ACS) troca o Wi-Fi dela
//   ZTEG = ZTE    -> troca pela OLT
// A troca de Wi-Fi por OMCI da ZTE nao funciona em Huawei (medido em campo),
// entao mandar uma Huawei para a OLT so geraria erro. Vai para o ACS.
const pOlt = prev.sgp_payload || {};
const serial = String(escolhida.phy_addr || '').toUpperCase();
if (pOlt.wifi_auto === true) {
  // Huawei: so o TR-069 (ACS) troca o Wi-Fi dela.
  if (serial.slice(0, 4) === 'HWTC') {
    const acsQ = montarAcsQuery(pOlt.login, pOlt.mac, escolhida.phy_addr);
    // Sem chave de juncao nao da para achar o device na NBI sem risco de pegar
    // o errado. Vira chamado, como qualquer outra falta de dado.
    if (!acsQ) return falha('sem_chave_acs');
    return [{ json: Object.assign({}, prev, {
      wifi_rota: 'acs', olt_onu: null, olt_falha: null,
      sgp_action: 'definir_wifi_acs',
      sgp_payload: Object.assign({}, pOlt, { acs_query: acsQ }),
    }) }];
  }
  // Fora ZTE, a troca pela OLT (OMCI da ZTE) so foi provada em ZTE. Mandar outra
  // marca para a OLT so geraria erro; vira chamado para a equipe resolver.
  if (serial.slice(0, 4) !== 'ZTEG') return falha('marca_nao_suportada', serial.slice(0, 4));
}

// ZTE (auto) ou modo olt explicito: escreve pela OLT. O chassi e 1 nas OLTs de
// prateleira unica; fica configuravel porque descobrir isso em producao e caro.
const shelf = String($env.OLT_SHELF || '1').trim();
const partes = [escolhida.slot, escolhida.pon, escolhida.onuid];
if (partes.some(function (v) { return v === null || v === undefined || v === ''; })) {
  return falha('porta_incompleta');
}

return [{ json: Object.assign({}, prev, {
  wifi_rota: 'olt',
  olt_onu: 'gpon_onu-' + shelf + '/' + partes[0] + '/' + partes[1] + ':' + partes[2],
  olt_falha: null,
}) }];
"""

# Nao aplicou porque nem chegamos a tentar. Do lado do cliente e tudo igual -
# ninguem mexeu no roteador dele - mas o motivo tem de ficar no log, porque e
# ele que diz o que corrigir no cadastro da ONU no SGP.
JS_WIFI_VIRA_CHAMADO = r"""
const prev = $input.first().json;
const p = prev.sgp_payload || {};

// Descobre por que nao deu, para o tecnico saber o que encontrar. O motivo vem
// de dois lugares diferentes: do node que monta o endereco (nao achou a ONU) ou
// da resposta do servico (a OLT recusou o comando).
let motivo = prev.olt_falha || null;
if (!motivo) {
  try {
    const r = $('OLT - Trocar Wifi').first().json;
    motivo = ((r && r.body && r.body.detalhe) || 'a OLT recusou o comando');
  } catch (e) { motivo = 'a OLT recusou o comando'; }
}

const det = ['Solicitacao de alteracao de Wi-Fi pelo atendimento automatico.',
             'O sistema TENTOU aplicar sozinho e nao conseguiu: ' + motivo + '.'];
if (p.ssid)  det.push('Novo nome da rede: ' + p.ssid);
if (p.senha) det.push('Nova senha: ' + p.senha);
det.push('Solicitado pelo WhatsApp ' + prev.phone + ', identidade validada.');

return [{ json: Object.assign({}, prev, {
  sgp_action: 'abrir_chamado',
  sgp_payload: { contrato: p.contrato, conteudo: det.join('\n') },
  wifi_virou_chamado: true,
  wifi_motivo: motivo,
}) }];
"""

# ---------------------------------------------------------------- Financeiro
JS_PROC_FATURA = r"""
const prev = $('Parse & Route').first().json;
const resp = $input.first().json;

// Resposta do SGP: { status, razaoSocial, links: [ {fatura, vencimento, valor,
// valor_original, linhadigitavel, link, link_cobranca, juros, multa} ] }
const todos = Array.isArray(resp && resp.links) ? resp.links : [];

// Regra do provedor: mostrar as VENCIDAS e a do mes atual; NUNCA as de meses a
// frente. O SGP pode ter boletos futuros ja gerados, e nao se oferece o cliente
// pagar adiantado - so o que esta vencido ou vence neste mes.
function anoMes(d) { return d.getFullYear() * 100 + (d.getMonth() + 1); }
function ymVenc(iso) {
  const m = String(iso || '').match(/^(\d{4})-(\d{2})/);
  // Sem data legivel: trata como antiga (mostra) - some-lo poderia esconder uma
  // fatura vencida de verdade, o que e pior que mostrar uma a mais.
  return m ? (Number(m[1]) * 100 + Number(m[2])) : 0;
}
const limiteYM = anoMes(new Date());
const links = todos.filter(function (f) { return ymVenc(f.vencimento) <= limiteYM; });

function brl(v) {
  const n = Number(v || 0);
  return 'R$ ' + n.toFixed(2).replace('.', ',');
}
function dataBR(iso) {
  const m = String(iso || '').match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? (m[3] + '/' + m[2] + '/' + m[1]) : String(iso || '');
}

let reply_text, next_step = 'menu';

if (!links.length) {
  reply_text = 'Boa notícia: você não tem nenhuma fatura em aberto no momento.\n\nDigite *menu* para voltar ao início.';
} else {
  // Mais antigas primeiro (as vencidas importam mais). Limita a 3 para nao
  // despejar uma parede de texto - base real pode ter dezenas de titulos.
  const ordenados = links.slice().sort(function (a, b) {
    return String(a.vencimento || '').localeCompare(String(b.vencimento || ''));
  });
  const mostrar = ordenados.slice(0, 3);

  const blocos = mostrar.map(function (f) {
    let t = '*Vencimento:* ' + dataBR(f.vencimento) + '\n*Valor:* ' + brl(f.valor);
    if (Number(f.juros || 0) > 0 || Number(f.multa || 0) > 0) {
      t += '  _(já com juros e multa)_';
    }
    // PIX copia-e-cola primeiro: e o jeito mais rapido de pagar e regularizar.
    // O bot so ENTREGA o codigo - quem paga e o cliente, no banco/app dele.
    if (f.codigopix) t += '\n\n*PIX copia e cola:*\n`' + f.codigopix + '`';
    if (f.linhadigitavel) t += '\n\n*Linha digitável (boleto):*\n`' + f.linhadigitavel + '`';
    if (f.link) t += '\n' + f.link;
    return t;
  });

  reply_text = (links.length > 3
      ? 'Você tem *' + links.length + '* faturas em aberto. Mostrando as ' + mostrar.length + ' mais antigas:\n\n'
      : (links.length === 1 ? 'Aqui está sua fatura em aberto:\n\n'
                            : 'Você tem *' + links.length + '* faturas em aberto:\n\n'))
    + blocos.join('\n\n---\n\n')
    + '\n\n_Depois de pagar, o acesso normaliza automaticamente assim que o ' +
      'pagamento é identificado (pode levar alguns minutos)._'
    + '\n\nDigite *menu* para voltar ao início.';

  if (links.length > 3) {
    reply_text += '\n_Para ver todas, fale com um atendente (opção 5)._';
  }
}

const session_patch = Object.assign({}, prev.session_patch, { reset: true });

return [{ json: Object.assign({}, prev, {
  reply_text: reply_text,
  next_step: next_step,
  session_patch: session_patch,
  _audit: {
    tipo: 'segunda_via',
    phone: prev.phone,
    cpf: prev.session.cpf,
    contrato: prev.sgp_payload.contrato,
    ssid_novo: null,
    sucesso: links.length > 0,
    // Nao guarda linha digitavel nem link no log de auditoria: sao dados de
    // pagamento. So o suficiente para rastrear a consulta.
    resposta_sgp: { status: resp && resp.status, qtd_faturas: links.length },
  },
}) }];
"""

# ---------------------------------------------------------------- Suporte
JS_PROC_CHAMADO = r"""
const prev = $('Parse & Route').first().json;
const resp = $input.first().json;

// Resposta do SGP: { status, razaoSocial, protocolo, cpfCnpj, contratoId, msg }
const protocolo = resp && resp.protocolo;
let reply_text, next_step;

// Chamado nascido de uma troca de Wi-Fi que nao pode ser aplicada sozinha:
// a pessoa escolheu uma senha e merece saber o que aconteceu com ela.
let veioDoWifi = false;
try { veioDoWifi = $('Wifi Vira Chamado').first().json.wifi_virou_chamado === true; } catch (e) { veioDoWifi = false; }

if (protocolo && veioDoWifi) {
  reply_text = 'Seu equipamento não aceita a alteração automática, então registrei ' +
    'o pedido para nossa equipe aplicar.\n\n*Protocolo:* ' + protocolo +
    '\n\nVocê será avisado quando estiver pronto. Sua rede continua como está até lá.\n\n' +
    'Digite *menu* para voltar ao início.';
  next_step = 'menu';
} else if (protocolo) {
  reply_text = 'Chamado aberto com sucesso!\n\n*Protocolo:* ' + protocolo +
    '\n\nNossa equipe vai analisar e entrar em contato. Guarde esse número para acompanhar.\n\n' +
    'Digite *menu* para voltar ao início.';
  next_step = 'menu';
} else {
  const msg = (resp && resp.msg) ? String(resp.msg) : '';
  reply_text = 'Não consegui abrir o chamado automaticamente' + (msg ? ' (' + msg + ')' : '') +
    '. Vou te transferir para um atendente.';
  next_step = 'human_handoff';
}

const session_patch = Object.assign({}, prev.session_patch, { reset: true });

return [{ json: Object.assign({}, prev, {
  reply_text: reply_text,
  next_step: next_step,
  session_patch: session_patch,
  _audit: {
    tipo: veioDoWifi ? 'wifi' : 'chamado',
    phone: prev.phone,
    cpf: prev.session.cpf,
    contrato: prev.sgp_payload.contrato,
    ssid_novo: protocolo ? ('protocolo:' + protocolo) : null,
    sucesso: !!protocolo,
    resposta_sgp: resp,
  },
}) }];
"""

# ---------------------------------------------------------------- Promessa
# O bot NAO cria promessa (a API URA so tem promessapagamento/list). Aqui ele
# CONSULTA: se ja existe uma promessa ativa, informa o cliente; se nao, encaminha
# ao atendente para registrar. Foi a regra pedida pelo provedor.
JS_PROC_PROMESSA = r"""
const prev = $('Parse & Route').first().json;
const resp = $input.first().json;

// O formato da lista varia entre versoes - aceita as formas comuns.
let lista = [];
if (Array.isArray(resp)) lista = resp;
else if (resp && Array.isArray(resp.promessas)) lista = resp.promessas;
else if (resp && Array.isArray(resp.list)) lista = resp.list;
else if (resp && Array.isArray(resp.results)) lista = resp.results;
else if (resp && Array.isArray(resp.data)) lista = resp.data;

// So conta promessa que ainda vale. Sem status legivel, considera que vale
// (melhor informar do que mandar pro atendente duplicar uma que ja existe).
function ativa(p) {
  const st = String((p && (p.status || p.situacao || p.estado)) || '').toLowerCase();
  if (!st) return true;
  return !/cancel|quebrad|expir|venc|conclu|paga|quit|finaliz|inativ/.test(st);
}
const ativas = lista.filter(ativa);

function dataBR(v) {
  const m = String(v || '').match(/(\d{4})-(\d{2})-(\d{2})/);
  return m ? (m[3] + '/' + m[2] + '/' + m[1]) : String(v || '');
}

let reply_text, next_step;
if (ativas.length > 0) {
  const p = ativas[0];
  const prazo = p.data_promessa || p.datapromessa || p.data || p.vencimento || p.prazo || '';
  reply_text = 'Você já tem uma *promessa de pagamento* registrada' +
    (prazo ? ' (prazo até *' + dataBR(prazo) + '*)' : '') + '.\n\n' +
    'Seu acesso deve seguir liberado até o prazo. Se ainda estiver sem internet, ' +
    'reinicie o roteador (tira da tomada, espera 30s e liga).\n\n' +
    'Para quitar agora, digite *1*. Digite *menu* para voltar.';
  next_step = 'regularizar';
} else {
  // Sem promessa ativa: o bot nao registra - encaminha ao atendente (fila).
  reply_text = 'Para registrar uma *promessa de pagamento* e liberar seu acesso, ' +
    'vou te encaminhar para um atendente.\n\nSe preferir já quitar, digite *1* para ' +
    'pagar por PIX ou boleto.';
  next_step = 'human_handoff';
}

return [{ json: Object.assign({}, prev, {
  reply_text: reply_text,
  next_step: next_step,
  // Nao reseta: o cliente pode digitar 1 em seguida para pagar sem repetir CPF.
  session_patch: Object.assign({}, prev.session_patch),
  _audit: {
    tipo: 'promessa',
    phone: prev.phone,
    cpf: prev.session.cpf,
    contrato: prev.sgp_payload.contrato,
    ssid_novo: null,
    sucesso: true,
    resposta_sgp: { promessas: lista.length, ativas: ativas.length },
  },
}) }];
"""

# ---------------------------------------------------------------- Diagnostico
JS_PROC_BUSCA_ONU = r"""
const prev = $('Parse & Route').first().json;

// A carga do diagnostico (contrato, mac, valor em aberto) chega por dois
// caminhos: reaproveitando a identidade, o proprio Parse & Route ja traz;
// quando pediu o CPF agora, quem traz e o Processar Consulta CPF - e ali o
// Parse & Route deste turno carrega so o 'lookup_cpf', sem contrato. Ler so o
// Parse & Route deixaria o filtro por contrato (e a auditoria) sem o contrato
// no caminho do CPF. Entao pega de quem realmente tiver a carga de diagnostico.
function jsonDe(node) { try { return $(node).first().json; } catch (e) { return null; } }
const viaCpf = jsonDe('Processar Consulta CPF');
const carga = (viaCpf && viaCpf.sgp_payload && viaCpf.sgp_payload.contrato != null)
  ? viaCpf.sgp_payload
  : ((prev.sgp_payload && prev.sgp_payload.contrato != null) ? prev.sgp_payload : (prev.sgp_payload || {}));

// /api/fttx/onu/list/ devolve um array. Filtrar por ?contrato= e o caminho
// natural, mas nem toda base tem esse vinculo preenchido - por isso o node
// seguinte tenta de novo por phy_addr (que casa com servico_mac do contrato).
// O n8n quebra resposta JSON que e array em VARIOS itens, um por elemento.
// Entao $input.first().json pode ser o array inteiro ou apenas a primeira ONU
// dele, conforme a versao e a configuracao do node. Ler so o primeiro item e
// testar Array.isArray da falso negativo: a lista vira vazia e o bot responde
// "nao localizei seu equipamento" com a ONU bem ali, vinculada ao contrato.
// Aconteceu em producao em 31/08/2026. Aceita as duas formas.
const itens = $input.all().map(function (i) { return i.json; });
let onus = [];
if (itens.length === 1 && Array.isArray(itens[0])) {
  onus = itens[0];
} else {
  onus = itens.filter(function (o) {
    return o && typeof o === 'object' && (o.slot !== undefined || o.id !== undefined);
  });
}
const mac = String(carga.mac || '').replace(/[^a-zA-Z0-9]/g, '').toLowerCase();
const alvoContrato = String(carga.contrato || '');

// O filtro por ?contrato= nem sempre e respeitado nesta base: as vezes o
// endpoint devolve a base inteira. Quando isso acontece e o desempate cai em
// onus[0], TODO cliente recebe o MESMO equipamento - foi exatamente o que o
// provedor relatou ("o diagnostico e sempre fixo, nao diferencia o aparelho").
// Por isso, vindo mais de uma ONU, reduzimos pelo contrato do proprio item
// antes de qualquer outra coisa. O item da lista carrega o vinculo em
// service_contrato (visto em producao).
function contratoDoItem(o) {
  const c = o.service_contrato != null ? o.service_contrato
          : (o.contrato != null ? o.contrato
          : (o.contrato_id != null ? o.contrato_id : null));
  return c == null ? '' : String(c);
}
let candidatas = onus;
if (onus.length > 1 && alvoContrato) {
  const doContrato = onus.filter(function (o) { return contratoDoItem(o) === alvoContrato; });
  if (doContrato.length) candidatas = doContrato;
}

function casaMac(o) {
  return mac && String(o.phy_addr || '').replace(/[^a-zA-Z0-9]/g, '').toLowerCase() === mac;
}

let escolhida = null;
if (candidatas.length === 1) {
  escolhida = candidatas[0];
} else if (candidatas.length > 1) {
  // Ainda ambiguo (o contrato nao afunilou para um so): so o MAC do contrato
  // pode desempatar. Sem casar o MAC, preferimos dizer "nao localizei" a
  // mostrar o aparelho de outro cliente - NUNCA cair em onus[0].
  escolhida = candidatas.filter(casaMac)[0] || null;
}

return [{ json: Object.assign({}, prev, {
  onu_id: escolhida ? escolhida.id : null,
  onu_basica: escolhida || null,
  // Garante contrato/mac/valor_aberto adiante mesmo no caminho do CPF, onde o
  // Parse & Route deste turno so tem o 'lookup_cpf'.
  sgp_payload: Object.assign({}, prev.sgp_payload, carga),
}) }];
"""

JS_PROC_DIAGNOSTICO = r"""
const prev = $('Processar Busca ONU').first().json;
const detalhe = $('SGP - ONU Detalhe').first().json;
const info = $input.first().json;

const onu = (detalhe && detalhe.onu) || {};
const base = prev.onu_basica || {};

function num(v) {
  if (v === null || v === undefined || v === '') return null;
  const n = parseFloat(String(v).replace(',', '.'));
  return isFinite(n) ? n : null;
}
// Faixa fisicamente plausivel para potencia RECEBIDA em GPON.
function naFaixa(v) { return v !== null && v < 0 && v >= -40; }

// O ONU Info abre um SSH na OLT e devolve o texto cru do terminal - o formato
// muda conforme o fabricante (Huawei, ZTE, Fiberhome, Datacom). Em vez de
// confiar num parser especifico, procuramos um valor em dBm e so aceitamos se
// cair na faixa fisicamente plausivel. Melhor dizer "indisponivel" do que
// mostrar um numero errado de sinal para o cliente.
function extrairSinal(txt) {
  const s = String(txt == null ? '' : txt);
  if (!s || /Exception|Traceback|Could not resolve|timeout/i.test(s)) return null;
  // Se nem fala de potencia, nao ha o que extrair
  if (!/dbm|power|potencia/i.test(s)) return null;

  // 1) Valor colado na unidade ("-22.07 dbm"): sem ambiguidade.
  const colados = [];
  const re = /(-?\d{1,2}(?:[.,]\d{1,2})?)\s*dbm/gi;
  let m;
  while ((m = re.exec(s)) !== null) colados.push(parseFloat(m[1].replace(',', '.')));
  const colNeg = colados.filter(naFaixa);
  if (colNeg.length) return colNeg[0];

  // 2) Formato tabular (Huawei e afins): a unidade fica no cabecalho e os
  //    valores vem embaixo. Aqui vale a fisica do GPON: a potencia RECEBIDA
  //    e negativa e a TRANSMITIDA e positiva. Entao, se houver exatamente um
  //    valor negativo na faixa plausivel, ele so pode ser o Rx.
  //    Com mais de um candidato, e ambiguo - preferimos nao responder a
  //    arriscar mostrar Tx (ou a potencia de outra ONU) como se fosse o sinal.
  const todos = [];
  const re2 = /(^|[\s:=(\[])(-\d{1,2}(?:[.,]\d{1,2})?)(?![\d.,])/g;
  while ((m = re2.exec(s)) !== null) todos.push(parseFloat(m[2].replace(',', '.')));
  const unicos = todos.filter(naFaixa).filter(function (v, i, a) { return a.indexOf(v) === i; });
  return unicos.length === 1 ? unicos[0] : null;
}

// Classificacao interna. O rotulo tecnico (dBm) NAO vai para o cliente: vira
// uma frase simples em portugues na resposta. So a auditoria guarda o numero.
function classificar(dbm) {
  if (dbm === null) return null;
  if (dbm >= -25) return { nivel: 'bom' };
  if (dbm >= -27) return { nivel: 'atencao' };
  return { nivel: 'ruim' };
}

// Ordem de preferencia para o sinal:
//   1) info_rx do /fttx/onu/list/ - o SGP ja coleta e guarda o valor numerico,
//      entao nao ha nada para adivinhar. E o caminho normal.
//   2) info_rx do detalhe da ONU, caso a lista venha sem.
//   3) so em ultimo caso, o texto cru da OLT via extrairSinal() - que erra
//      facil e por isso prefere devolver null a chutar.
let dbm = null;
let origem = null;
if (naFaixa(num(base.info_rx))) { dbm = num(base.info_rx); origem = 'lista'; }
if (dbm === null && naFaixa(num(onu.info_rx))) { dbm = num(onu.info_rx); origem = 'detalhe'; }
if (dbm === null) {
  dbm = extrairSinal(info && (info.result !== undefined ? info.result : info));
  if (dbm !== null) origem = 'olt';
}
const cls = classificar(dbm);

// info_date: "2026-08-07 07:02:09". Uma leitura de dias atras nao descreve a
// conexao de agora - se estiver velha, avisa em vez de apresentar como atual.
const medidoEm = String(base.info_date || onu.info_date || '');
let horasAtras = null;
const md = medidoEm.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/);
if (md) {
  const t = Date.UTC(+md[1], +md[2] - 1, +md[3], +md[4], +md[5]);
  horasAtras = (Date.now() - t) / 3600000;
}

// O cliente recebe uma leitura simples da conexao, sem jargao (nada de dBm,
// CTO ou modelo do aparelho) - foi o pedido do provedor: "a mensagem esta
// muito tecnica, tem que ser para o cliente". Os dados tecnicos seguem so na
// auditoria, para o suporte investigar.
const REBOOT = 'desligue o roteador da tomada, espere 30 segundos e ligue de novo';
const temEquip = !!(base.type || onu.modelo || dbm !== null);

// Corte por falta de pagamento: quando a base mantem o contrato Ativo e bloqueia
// no RADIUS/OLT, o sinal otico continua "bom" (o equipamento segue na fibra),
// mas o cliente fica sem internet. Se ha valor em aberto, avisamos - foi o
// pedido do provedor. (A suspensao formal, status 4, ja e avisada na
// identificacao.)
const valorAberto = Number((prev.sgp_payload && prev.sgp_payload.valor_aberto) || 0);

let reply_text;
if (!cls) {
  // ONU localizada, mas nao deu para confirmar o sinal (OLT fora do ar etc.)
  reply_text = 'Fiz um teste na sua conexão, mas não consegui confirmar o sinal ' +
    'da sua internet agora.\n\n' +
    'Tente o seguinte: ' + REBOOT + '. Se não resolver, digite *3* para abrir ' +
    'um chamado ou *5* para falar com um atendente.';
} else if (cls.nivel === 'bom' && valorAberto > 0) {
  // Sinal bom + divida: classico bloqueio no RADIUS por falta de pagamento. O
  // pagamento domina a resposta - de nada adianta falar de reiniciar o roteador.
  reply_text = 'Seu equipamento está conectado e o sinal da sua fibra está bom — ' +
    'do nosso lado a conexão está no ar.\n\n⚠️ Mas há *valores em aberto* no seu ' +
    'contrato. Se você está sem internet, é muito provavelmente um *bloqueio por ' +
    'falta de pagamento*.\n\nDigite *2* para ver o seu boleto — a conexão volta ' +
    'sozinha assim que o pagamento é identificado.';
} else if (cls.nivel === 'bom') {
  if (horasAtras !== null && horasAtras > 48) {
    reply_text = '✅ Testei a sua conexão e, na última verificação, o sinal estava ' +
      'normal.\n\nSe você está com problema agora, ' + REBOOT + '. Se continuar, ' +
      'digite *3* para abrir um chamado.';
  } else {
    reply_text = '✅ Boa notícia! Testei a sua conexão de fibra e está tudo certo ' +
      'por aqui.\n\nSe mesmo assim a internet estiver lenta ou caindo, ' + REBOOT +
      '. Se não resolver, digite *3* para abrir um chamado.';
  }
} else if (cls.nivel === 'atencao') {
  reply_text = '⚠️ Sua internet está funcionando, mas o sinal está um pouco fraco ' +
    'e pode oscilar em dias de chuva.\n\nSe estiver enfrentando quedas, digite ' +
    '*3* para abrir um chamado e agendarmos uma visita técnica.';
} else {
  reply_text = '🔴 Encontrei um problema na sua conexão: o sinal da sua internet ' +
    'está fraco e o ideal é uma visita técnica.\n\nDigite *3* para abrir um ' +
    'chamado que a gente resolve para você.';
}

// Nos demais casos (sinal ruim/atencao, ou sem leitura), se ainda houver valor
// em aberto, acrescenta a ressalva - o bloqueio por pagamento pode conviver com
// um problema de sinal. O caso "sinal bom + divida" ja foi tratado acima.
if (valorAberto > 0 && !(cls && cls.nivel === 'bom')) {
  reply_text += '\n\n⚠️ Também há *valores em aberto* no seu contrato. Se a sua ' +
    'internet está bloqueada, pode ser por falta de pagamento — digite *2* para ' +
    'ver o seu boleto.';
}
reply_text += '\n\nDigite *menu* para voltar ao início.';

const session_patch = Object.assign({}, prev.session_patch, { reset: true });

return [{ json: Object.assign({}, prev, {
  reply_text: reply_text,
  next_step: 'menu',
  session_patch: session_patch,
  _audit: {
    tipo: 'diagnostico',
    phone: prev.phone,
    cpf: prev.session.cpf,
    contrato: prev.sgp_payload.contrato,
    ssid_novo: null,
    sucesso: temEquip,
    resposta_sgp: { onu_id: prev.onu_id, cto: onu.cto || null, sinal_dbm: dbm,
                    sinal_origem: origem, medido_em: medidoEm || null,
                    valor_aberto: valorAberto },
  },
}) }];
"""

JS_ONU_NAO_ENCONTRADA = r"""
const prev = $input.first().json;
return [{ json: Object.assign({}, prev, {
  reply_text: 'Não localizei o equipamento de fibra vinculado ao seu contrato. ' +
    'Isso pode acontecer se sua conexão não for por fibra óptica.\n\n' +
    'Digite *3* para abrir um chamado ou *5* para falar com um atendente.',
  next_step: 'menu',
  session_patch: Object.assign({}, prev.session_patch, { reset: true }),
}) }];
"""


# ---------------------------------------------------------------- Persistencia
JS_PERSIST = r"""
const item = $input.first().json;
const patch = item.session_patch || {};
// patch.reset limpa a sessao inteira (fim de atendimento ou cliente digitou "menu")
const merged = patch.reset ? {} : Object.assign({}, item.session || {}, patch);
delete merged.reset;
Object.keys(merged).forEach(function (k) { if (merged[k] === undefined) delete merged[k]; });

// Chegar aqui sem texto e bug: todo caminho deveria ter montado uma resposta.
// So que mandar texto vazio faz a Evolution responder 400, o node de envio
// quebra e o cliente fica sem NADA - o pior desfecho possivel. Entao troca por
// uma saida generica e deixa rastro no log para a gente achar o caminho furado.
let texto = item.reply_text;
if (texto === null || texto === undefined || String(texto).trim() === '') {
  console.log('[bug] reply_text vazio | phone=' + (item.phone || '?') +
              ' step=' + (item.next_step || '?') + ' acao=' + (item.sgp_action || '?'));
  texto = 'Tive um problema para montar a resposta agora. Digite *menu* para ' +
          'recomeçar ou *5* para falar com um atendente.';
}

// Registro da conversa (aba do painel). O que o cliente digitou vem do Extract
// Inbound; a resposta e o proprio texto. Mascara o sensivel: a senha do Wi-Fi e
// a data de nascimento (2FA). O painel e lido pela equipe toda, e essas duas
// nunca podem ficar em claro - o resto do dialogo fica visivel.
let msgEntrada = '';
try { msgEntrada = String(($('Extract Inbound').first().json || {}).text || ''); } catch (e) { msgEntrada = ''; }
let stepAntes = '';
try { stepAntes = String(($('Get Session').first().json || {}).step || ''); } catch (e) { stepAntes = ''; }
if (stepAntes === 'awaiting_password' || stepAntes === 'awaiting_second_factor') {
  msgEntrada = '••••••';
}

return [{
  json: {
    phone: item.phone,
    step: item.next_step,
    data: JSON.stringify(merged),
    reply_text: texto,
    audit: item._audit ? JSON.stringify(item._audit) : null,
    msg_in: msgEntrada,
    msg_out: texto,
    msg_contrato: (merged && merged.contrato != null) ? String(merged.contrato) : '',
  }
}];
"""


def code_node(node_id, name, js, pos):
    return {"parameters": {"jsCode": js.strip()}, "id": node_id, "name": name,
            "type": "n8n-nodes-base.code", "typeVersion": 2, "position": pos}


def cond(left, right):
    return {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict"},
            "conditions": [{"leftValue": left, "rightValue": right,
                            "operator": {"type": "string", "operation": "equals"}}],
            "combinator": "and"}


PG_CRED = {"postgres": {"id": "REPLACE_ME", "name": "Postgres - botSgp"}}
SGP_AUTH = [{"name": "token", "value": "={{ $env.SGP_API_TOKEN }}"},
            {"name": "app", "value": "={{ $env.SGP_APP_NAME }}"}]

nodes = [
    # webhookId e obrigatorio: sem ele o n8n registra a rota como
    # {workflowId}/{nome-do-node}/{path} em vez de so /webhook/{path}.
    # A interface gera esse UUID sozinha; num JSON montado por fora, nao.
    {"parameters": {"httpMethod": "POST", "path": "evolution-inbound",
                    "responseMode": "onReceived", "options": {}},
     "id": "webhook-evolution", "name": "Webhook Evolution API",
     "webhookId": "7f3c9e2a-5b41-4d8e-9a06-1c2f4b8d6e30",
     "type": "n8n-nodes-base.webhook", "typeVersion": 2, "position": [0, 0]},

    code_node("code-extract", "Extract Inbound", JS_EXTRACT, [200, 0]),

    # A sessao guarda CPF, contrato e o verified_at que dispensa revalidacao.
    # Expirar isso nao pode depender de um cron que alguem lembrou de agendar:
    # a propria consulta ignora o que passou de 30 min e apaga as sessoes
    # abandonadas de todo mundo no mesmo golpe. A janela de identidade e de 15
    # min, entao 30 aqui nunca corta um atendimento que ainda valeria.
    {"parameters": {"operation": "executeQuery",
                    "query": ("WITH expiradas AS (\n"
                              "    DELETE FROM wa_sessions WHERE updated_at < now() - interval '30 minutes'\n"
                              ")\n"
                              "SELECT s.step, s.data,\n"
                              # O bot so CALA quando um atendente assumiu de fato (atendente
                              # preenchido no painel). Se o cliente so entrou na fila
                              # (ativo=true, atendente NULL), o bot continua respondendo -
                              # fazendo companhia ate alguem assumir.
                              "       COALESCE((SELECT ativo AND atendente IS NOT NULL\n"
                              "                   FROM wa_humano WHERE phone = $1), false) AS humano\n"
                              "FROM (\n"
                              "  SELECT step, data FROM wa_sessions\n"
                              "   WHERE phone = $1 AND updated_at >= now() - interval '30 minutes'\n"
                              "  UNION ALL\n"
                              "  SELECT NULL, NULL WHERE NOT EXISTS (\n"
                              "      SELECT 1 FROM wa_sessions\n"
                              "       WHERE phone = $1 AND updated_at >= now() - interval '30 minutes')\n"
                              ") s"),
                    "options": {"queryReplacement": "={{ [$json.phone] }}"}},
     "id": "pg-get", "name": "Get Session", "type": "n8n-nodes-base.postgres",
     # O UNION ALL garante que o node devolva 1 item mesmo se o cliente for novo,
     # impedindo que o n8n aborte silenciosamente o fluxo aqui por falta de dados.
     "alwaysOutputData": True,
     "typeVersion": 2.4, "position": [400, 0], "credentials": PG_CRED},

    # Atendimento humano: se 'humano' esta ligado (o cliente pediu atendente ou
    # o atendente assumiu pelo painel), o BOT NAO responde. So registra a
    # mensagem do cliente para o painel e para. Quem responde e a pessoa.
    {"parameters": {
        "conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose"},
                       "conditions": [{"leftValue": "={{ $json.humano }}", "rightValue": True,
                                       "operator": {"type": "boolean", "operation": "true",
                                                    "singleValue": True}}],
                       "combinator": "and"}, "options": {}},
     "id": "if-humano", "name": "Em atendimento humano?", "type": "n8n-nodes-base.if",
     "typeVersion": 2.2, "position": [560, 0]},

    # Em modo humano so a mensagem do cliente e gravada (para o painel). Sem
    # mascara: nao ha prompt de senha aqui, e o cliente esta conversando livre.
    {"parameters": {"operation": "executeQuery",
                    "query": ("INSERT INTO wa_messages (phone, direcao, texto)\n"
                              "SELECT $1, 'in', $2 WHERE NULLIF($2, '') IS NOT NULL\n"
                              "RETURNING 1;"),
                    "options": {"queryReplacement":
                        "={{ [$('Extract Inbound').first().json.phone, "
                        "$('Extract Inbound').first().json.text] }}"}},
     "id": "pg-msg-humano", "name": "Registrar Entrada Humano",
     "type": "n8n-nodes-base.postgres", "onError": "continueRegularOutput",
     "alwaysOutputData": True, "typeVersion": 2.4, "position": [760, 160],
     "credentials": PG_CRED},

    code_node("code-route", "Parse & Route", JS_PARSE_ROUTE, [760, 0]),

    # Switch principal: o que a mensagem do cliente disparou
    {"parameters": {"rules": {"values": [
        {"conditions": cond("={{ $json.sgp_action }}", "lookup_cpf"),
         "renameOutput": True, "outputKey": "lookup_cpf"},
        {"conditions": cond("={{ $json.sgp_action }}", "definir_wifi"),
         "renameOutput": True, "outputKey": "definir_wifi"},
        {"conditions": cond("={{ $json.sgp_action }}", "definir_wifi_acs"),
         "renameOutput": True, "outputKey": "definir_wifi_acs"},
        {"conditions": cond("={{ $json.sgp_action }}", "definir_wifi_olt"),
         "renameOutput": True, "outputKey": "definir_wifi_olt"},
        {"conditions": cond("={{ $json.sgp_action }}", "abrir_chamado"),
         "renameOutput": True, "outputKey": "abrir_chamado"},
        {"conditions": cond("={{ $json.sgp_action }}", "segunda_via"),
         "renameOutput": True, "outputKey": "segunda_via"},
        # Sem esta saida, o diagnostico com identidade JA validada (reaproveitada)
        # caia no fallback e nao fazia nada - o cliente escolhia 4 e nao vinha
        # resposta. So funcionava quando pedia CPF na hora (outro switch).
        {"conditions": cond("={{ $json.sgp_action }}", "diagnostico"),
         "renameOutput": True, "outputKey": "diagnostico"},
        {"conditions": cond("={{ $json.sgp_action }}", "promessa"),
         "renameOutput": True, "outputKey": "promessa"},
    ]}, "options": {"fallbackOutput": "extra", "renameFallbackOutput": "sem_chamada"}},
     "id": "switch-action", "name": "Precisa chamar o SGP?",
     "type": "n8n-nodes-base.switch", "typeVersion": 3.2, "position": [800, 0]},

    # ---- Consulta de cliente (compartilhada pelos tres modulos) ----
    {"parameters": {
        "method": "POST", "url": "={{ $env.SGP_API_URL }}/api/ura/consultacliente/",
        "sendBody": True, "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify({ app: $env.SGP_APP_NAME, token: $env.SGP_API_TOKEN, cpfcnpj: $json.sgp_payload.cpf }) }}",
        "options": {"response": {"response": {"neverError": True}}, "timeout": 20000}},
     "id": "http-lookup", "name": "SGP - Consultar Cliente",
     "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [1000, -320]},

    code_node("code-proc-cpf", "Processar Consulta CPF", JS_PROC_CPF, [1200, -320]),

    # Financeiro e diagnostico ainda precisam de mais uma chamada ao SGP
    # depois que a identidade e confirmada - por isso este segundo switch.
    {"parameters": {"rules": {"values": [
        {"conditions": cond("={{ $json.sgp_action }}", "segunda_via"),
         "renameOutput": True, "outputKey": "segunda_via"},
        {"conditions": cond("={{ $json.sgp_action }}", "diagnostico"),
         "renameOutput": True, "outputKey": "diagnostico"},
    ]}, "options": {"fallbackOutput": "extra", "renameFallbackOutput": "responder"}},
     "id": "switch-pos-id", "name": "Mais alguma chamada?",
     "type": "n8n-nodes-base.switch", "typeVersion": 3.2, "position": [1400, -320]},

    # ---- Modulo 1: Wi-Fi ----
    # Corpo montado como texto em vez de campos fixos: com bodyParameters todos
    # os cinco campos viajam sempre, e quem so queria trocar a senha mandaria
    # novo_ssid vazio - o que APAGA o nome da rede em vez de manter. O
    # sgp_payload.form ja vem com apenas o que o cliente pediu para mudar.
    {"parameters": {
        "method": "POST", "url": "={{ $env.SGP_API_URL }}/api/ura/cpemanage/",
        "sendBody": True, "contentType": "raw",
        "rawContentType": "application/x-www-form-urlencoded",
        "body": ("={{ 'token=' + encodeURIComponent($env.SGP_API_TOKEN) +"
                 " '&app=' + encodeURIComponent($env.SGP_APP_NAME) +"
                 " '&' + $json.sgp_payload.form }}"),
        "options": {"response": {"response": {"neverError": True}}, "timeout": 30000}},
     "id": "http-wifi", "name": "SGP - Definir Wifi",
     "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [1000, 0]},

    # ---- Modulo 1b: Wi-Fi direto na NBI do GenieACS (WIFI_MODO=genieacs) ----
    # Sem projection de proposito: o device doc inteiro e maior, mas a busca
    # devolve no maximo um equipamento, e restringir campos aqui e a diferenca
    # entre "nao achei WLANConfiguration porque o modelo nao tem" e "nao achei
    # porque pedi errado" - dois diagnosticos opostos para o mesmo sintoma.
    {"parameters": {
        "method": "GET", "url": "={{ $env.GENIEACS_NBI_URL }}/devices/",
        "sendQuery": True,
        "queryParameters": {"parameters": [
            {"name": "query", "value": "={{ $json.sgp_payload.acs_query }}"}]},
        "options": {"response": {"response": {"neverError": True, "fullResponse": True}},
                    "timeout": 20000}},
     "id": "http-acs-busca", "name": "GenieACS - Buscar Device",
     "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [1000, 120]},

    code_node("code-acs-tarefa", "Montar Tarefa Wifi", JS_MONTAR_TAREFA_ACS, [1200, 120]),

    {"parameters": {
        "rules": {"values": [
            {"conditions": {"options": {"caseSensitive": True, "typeValidation": "loose"},
                            "conditions": [{"leftValue": "={{ $json.wifi_rota_acs }}", "rightValue": "aplicar",
                                            "operator": {"type": "string", "operation": "equals"}}],
                            "combinator": "and"},
             "renameOutput": True, "outputKey": "aplicar"},
            {"conditions": {"options": {"caseSensitive": True, "typeValidation": "loose"},
                            "conditions": [{"leftValue": "={{ $json.wifi_rota_acs }}", "rightValue": "chamado",
                                            "operator": {"type": "string", "operation": "equals"}}],
                            "combinator": "and"},
             "renameOutput": True, "outputKey": "chamado"}]},
        "options": {"fallbackOutput": "extra"}},
     "id": "switch-rota-acs", "name": "Rota do ACS", "type": "n8n-nodes-base.switch",
     "typeVersion": 3, "position": [1400, 120]},

    # connection_request faz o ACS acordar o equipamento agora em vez de
    # esperar o proximo inform periodico - e o que permite responder ao cliente
    # "ja esta valendo" em vez de "deve aplicar em algum momento". Quando o
    # roteador nao atende, a NBI responde 202 e a tarefa fica na fila; quem
    # traduz isso para o cliente e o "Processar Definir Wifi".
    {"parameters": {
        "method": "POST",
        "url": ("={{ $env.GENIEACS_NBI_URL }}/devices/"
                "{{ encodeURIComponent($json.acs_device_id) }}"
                "/tasks?connection_request&timeout=20000"),
        "sendBody": True, "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify($json.acs_task) }}",
        "options": {"response": {"response": {"neverError": True, "fullResponse": True}},
                    "timeout": 45000}},
     "id": "http-acs-aplicar", "name": "GenieACS - Aplicar Wifi",
     "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [1600, 60]},

    code_node("code-acs-falhou", "ACS Nao Aplicou", JS_ACS_NAO_APLICOU, [1600, 200]),

    # ---- Modulo 1c: Wi-Fi pela OLT (WIFI_MODO=olt) ----
    # Mesmo endpoint que o diagnostico usa: e o SGP que sabe em que slot/pon/onu
    # o assinante esta. Aqui ele serve para montar o endereco que a OLT entende.
    {"parameters": {
        "method": "GET", "url": "={{ $env.SGP_API_URL }}/api/fttx/onu/list/",
        "sendQuery": True,
        "queryParameters": {"parameters": [
            {"name": "token", "value": "={{ $env.SGP_API_TOKEN }}"},
            {"name": "app", "value": "={{ $env.SGP_APP_NAME }}"},
            {"name": "contrato", "value": "={{ $json.sgp_payload.contrato }}"}]},
        "options": {"response": {"response": {"neverError": True}}, "timeout": 25000}},
     "alwaysOutputData": True,
     "id": "http-onu-contrato", "name": "SGP - ONU do Contrato",
     "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [1000, 340]},

    code_node("code-montar-olt", "Montar Troca na OLT", JS_MONTAR_OLT, [1200, 340]),

    # Roteia por marca do aparelho (definida em "Montar Troca na OLT"): ZTE vai
    # para a OLT, Huawei para o ACS, e o que nao deu para resolver vira chamado.
    # No modo olt puro so saem 'olt' e 'chamado' - o caminho do ACS fica inerte.
    {"parameters": {
        "rules": {"values": [
            {"conditions": {"options": {"caseSensitive": True, "typeValidation": "loose"},
                            "conditions": [{"leftValue": "={{ $json.wifi_rota }}", "rightValue": "olt",
                                            "operator": {"type": "string", "operation": "equals"}}],
                            "combinator": "and"},
             "renameOutput": True, "outputKey": "olt"},
            {"conditions": {"options": {"caseSensitive": True, "typeValidation": "loose"},
                            "conditions": [{"leftValue": "={{ $json.wifi_rota }}", "rightValue": "acs",
                                            "operator": {"type": "string", "operation": "equals"}}],
                            "combinator": "and"},
             "renameOutput": True, "outputKey": "acs"},
            {"conditions": {"options": {"caseSensitive": True, "typeValidation": "loose"},
                            "conditions": [{"leftValue": "={{ $json.wifi_rota }}", "rightValue": "chamado",
                                            "operator": {"type": "string", "operation": "equals"}}],
                            "combinator": "and"},
             "renameOutput": True, "outputKey": "chamado"}]},
        "options": {"fallbackOutput": "extra"}},
     "id": "switch-rota-wifi", "name": "Rota do Wi-Fi", "type": "n8n-nodes-base.switch",
     "typeVersion": 3, "position": [1400, 340]},

    # O bot NAO fala com a OLT: fala com o servico de olt-wifi/, que e quem tem a
    # credencial e quem monta os comandos. Se este bot for comprometido, o que o
    # atacante alcanca e este endpoint - trocar o Wi-Fi de uma ONU - e nao a
    # configuracao da rede inteira do provedor.
    # Timeout alto porque cada chamada abre uma sessao SSH na OLT.
    {"parameters": {
        "method": "POST", "url": "={{ $env.OLT_WIFI_URL }}/trocar-wifi",
        "sendBody": True, "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify({ onu: $json.olt_onu, ssid: $json.sgp_payload.ssid, senha: $json.sgp_payload.senha }) }}",
        "sendHeaders": True,
        "headerParameters": {"parameters": [
            {"name": "X-Token", "value": "={{ $env.OLT_WIFI_TOKEN }}"}]},
        "options": {"response": {"response": {"neverError": True, "fullResponse": True}},
                    "timeout": 60000}},
     "id": "http-olt-wifi", "name": "OLT - Trocar Wifi",
     "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [1600, 280]},

    # A OLT nao usa codigo de saida util: quem diz se aplicou e o corpo que o
    # servico devolve. Este IF e o que separa "trocou" de "vira chamado".
    {"parameters": {
        "conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose"},
                       "conditions": [{"leftValue": "={{ $json.body.ok }}", "rightValue": True,
                                       "operator": {"type": "boolean", "operation": "true",
                                                    "singleValue": True}}],
                       "combinator": "and"}, "options": {}},
     "id": "if-olt-ok", "name": "Aplicou na OLT?", "type": "n8n-nodes-base.if",
     "typeVersion": 2.2, "position": [1750, 280]},

    code_node("code-vira-chamado", "Wifi Vira Chamado", JS_WIFI_VIRA_CHAMADO, [1600, 420]),

    code_node("code-proc-wifi", "Processar Definir Wifi", JS_PROC_WIFI, [1800, 0]),

    # ---- Modulo 2: Financeiro (2a via) ----
    # nao_gerar_os=1: sem isso o SGP abre uma ordem de servico a cada consulta,
    # o que entupiria a fila de atendimento com pedidos automaticos de boleto.
    {"parameters": {
        "method": "POST", "url": "={{ $env.SGP_API_URL }}/api/ura/fatura2via/",
        "sendBody": True, "contentType": "form-urlencoded",
        "bodyParameters": {"parameters": SGP_AUTH + [
            {"name": "contrato", "value": "={{ $json.sgp_payload.contrato }}"},
            {"name": "nao_gerar_os", "value": "1"}]},
        "options": {"response": {"response": {"neverError": True}}, "timeout": 25000}},
     "id": "http-fatura", "name": "SGP - Segunda Via",
     "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [1600, -200]},

    code_node("code-proc-fatura", "Processar Segunda Via", JS_PROC_FATURA, [1800, -200]),

    # ---- Regularizar: promessa de pagamento (so consulta; nao cria) ----
    # A API URA tem promessapagamento/list. Manda contrato+auth; neverError para
    # que uma resposta ruim vire "sem promessa" -> atendente, nunca um erro cru.
    {"parameters": {
        "method": "POST", "url": "={{ $env.SGP_API_URL }}/api/ura/promessapagamento/list",
        "sendBody": True, "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify({ app: $env.SGP_APP_NAME, token: $env.SGP_API_TOKEN, contrato: $json.sgp_payload.contrato }) }}",
        "options": {"response": {"response": {"neverError": True}}, "timeout": 20000}},
     "alwaysOutputData": True,
     "id": "http-promessa", "name": "SGP - Promessas",
     "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [1600, -60]},

    code_node("code-proc-promessa", "Processar Promessa", JS_PROC_PROMESSA, [1800, -60]),

    # ---- Modulo 3: Suporte (abrir chamado) ----
    {"parameters": {
        "method": "POST", "url": "={{ $env.SGP_API_URL }}/api/ura/chamado/",
        "sendBody": True, "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify({ token: $env.SGP_API_TOKEN, app: $env.SGP_APP_NAME, contrato: $json.sgp_payload.contrato, conteudo: $json.sgp_payload.conteudo, ocorrenciatipo: Number($env.SGP_OCORRENCIA_TIPO) }) }}",
        "options": {"response": {"response": {"neverError": True}}, "timeout": 25000}},
     "id": "http-chamado", "name": "SGP - Abrir Chamado",
     "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [1000, 220]},

    code_node("code-proc-chamado", "Processar Chamado", JS_PROC_CHAMADO, [1200, 220]),

    # ---- Modulo 4: Diagnostico da conexao (FTTH) ----
    # Filtro por contrato: e o vinculo natural, mas nem toda base preenche.
    # O node seguinte cobre o caso vazio caindo para busca por MAC.
    {"parameters": {
        "method": "GET", "url": "={{ $env.SGP_API_URL }}/api/fttx/onu/list/",
        "sendQuery": True,
        "queryParameters": {"parameters": [
            {"name": "token", "value": "={{ $env.SGP_API_TOKEN }}"},
            {"name": "app", "value": "={{ $env.SGP_APP_NAME }}"},
            {"name": "contrato", "value": "={{ $json.sgp_payload.contrato }}"}]},
        "options": {"response": {"response": {"neverError": True}}, "timeout": 25000}},
     "alwaysOutputData": True,
     "id": "http-onu-list", "name": "SGP - Buscar ONU",
     "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [1600, -440]},

    code_node("code-busca-onu", "Processar Busca ONU", JS_PROC_BUSCA_ONU, [1800, -440]),

    {"parameters": {
        "conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose"},
                       "conditions": [{"leftValue": "={{ $json.onu_id }}", "rightValue": "",
                                       "operator": {"type": "number", "operation": "exists",
                                                    "singleValue": True}}],
                       "combinator": "and"}, "options": {}},
     "id": "if-onu", "name": "Achou a ONU?", "type": "n8n-nodes-base.if",
     "typeVersion": 2.2, "position": [2000, -440]},

    {"parameters": {
        "method": "GET", "url": "={{ $env.SGP_API_URL }}/api/fttx/onu/{{ $json.onu_id }}/",
        "sendQuery": True,
        "queryParameters": {"parameters": [
            {"name": "token", "value": "={{ $env.SGP_API_TOKEN }}"},
            {"name": "app", "value": "={{ $env.SGP_APP_NAME }}"}]},
        "options": {"response": {"response": {"neverError": True}}, "timeout": 25000}},
     "id": "http-onu-detalhe", "name": "SGP - ONU Detalhe",
     "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [2200, -520]},

    # Este endpoint abre um SSH ao vivo na OLT: e o mais lento do fluxo e o
    # que mais falha (OLT fora do ar, hostname errado). neverError garante que
    # o cliente receba pelo menos os dados estruturados de CTO e equipamento.
    {"parameters": {
        "method": "GET",
        "url": "={{ $env.SGP_API_URL }}/api/fttx/onu/{{ $('Processar Busca ONU').first().json.onu_id }}/info/",
        "sendQuery": True,
        "queryParameters": {"parameters": [
            {"name": "token", "value": "={{ $env.SGP_API_TOKEN }}"},
            {"name": "app", "value": "={{ $env.SGP_APP_NAME }}"}]},
        "options": {"response": {"response": {"neverError": True}}, "timeout": 45000}},
     "id": "http-onu-info", "name": "SGP - ONU Info",
     "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [2400, -520]},

    code_node("code-proc-diag", "Processar Diagnostico", JS_PROC_DIAGNOSTICO, [2600, -520]),
    code_node("code-onu-404", "ONU Nao Encontrada", JS_ONU_NAO_ENCONTRADA, [2200, -360]),

    # ---- Persistencia e resposta ----
    code_node("code-persist", "Preparar Persistencia", JS_PERSIST, [2050, 0]),

    {"parameters": {"operation": "executeQuery",
                    "query": ("INSERT INTO wa_sessions (phone, step, data, updated_at)\n"
                              "VALUES ($1, $2, $3::jsonb, now())\n"
                              "ON CONFLICT (phone) DO UPDATE\n"
                              "  SET step = EXCLUDED.step, data = EXCLUDED.data, updated_at = now()\n"
                              "RETURNING 1;"),
                    "options": {"queryReplacement": "={{ [$json.phone, $json.step, $json.data] }}"}},
     "id": "pg-upsert", "name": "Upsert Session", "type": "n8n-nodes-base.postgres",
     # RETURNING 1 garante que o node passe 1 item para frente, caso contrario
     # o n8n encerra o fluxo e a mensagem de resposta nunca e enviada.
     "alwaysOutputData": True,
     "typeVersion": 2.4, "position": [2250, 0], "credentials": PG_CRED},

    {"parameters": {
        "conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose"},
                       "conditions": [{"leftValue": "={{ $('Preparar Persistencia').first().json.audit }}",
                                       "rightValue": "",
                                       "operator": {"type": "string", "operation": "notEmpty",
                                                    "singleValue": True}}],
                       "combinator": "and"}, "options": {}},
     "id": "if-audit", "name": "Tem auditoria?", "type": "n8n-nodes-base.if",
     "typeVersion": 2.2, "position": [2450, 0]},

    {"parameters": {"operation": "executeQuery",
                    "query": ("INSERT INTO wa_wifi_change_log\n"
                              "  (phone, cpf, contrato_id, ssid_novo, sucesso, resposta_sgp, tipo)\n"
                              "SELECT a->>'phone', a->>'cpf', a->>'contrato', a->>'ssid_novo',\n"
                              "       (a->>'sucesso')::boolean, a->'resposta_sgp',\n"
                              "       COALESCE(a->>'tipo', 'wifi')\n"
                              "FROM (SELECT $1::jsonb AS a) t\n"
                              "RETURNING 1;"),
                    "options": {"queryReplacement": "={{ [$('Preparar Persistencia').first().json.audit] }}"}},
     "id": "pg-audit", "name": "Gravar Auditoria", "type": "n8n-nodes-base.postgres",
     "typeVersion": 2.4, "position": [2650, -120], "credentials": PG_CRED},

    # Registro da conversa para o painel. Ramo PARALELO ao envio (sai do Upsert
    # Session junto com o "Tem auditoria?"), entao nunca atrasa nem impede a
    # resposta ao cliente. onError continua: se o log falhar, o atendimento
    # segue - a mensagem ja foi/sera enviada de qualquer jeito.
    # Insere as duas pontas numa tacada; linha vazia nao entra (WHERE).
    {"parameters": {"operation": "executeQuery",
                    "query": ("INSERT INTO wa_messages (phone, direcao, texto, contrato)\n"
                              "SELECT $1, d, t, NULLIF($4, '')\n"
                              "FROM (VALUES ('in', $2::text), ('out', $3::text)) v(d, t)\n"
                              "WHERE t IS NOT NULL AND t <> ''\n"
                              "RETURNING 1;"),
                    "options": {"queryReplacement":
                        "={{ [$('Preparar Persistencia').first().json.phone, "
                        "$('Preparar Persistencia').first().json.msg_in, "
                        "$('Preparar Persistencia').first().json.msg_out, "
                        "$('Preparar Persistencia').first().json.msg_contrato] }}"}},
     "id": "pg-msgs", "name": "Registrar Mensagens", "type": "n8n-nodes-base.postgres",
     "onError": "continueRegularOutput", "alwaysOutputData": True,
     "typeVersion": 2.4, "position": [2650, 160], "credentials": PG_CRED},

    # Fila de atendimento. Entra na fila quando o passo NOVO ($2) e human_handoff
    # (ativo=true); SAI da fila quando o passo ANTERIOR ($3) era human_handoff mas
    # o novo nao e - ou seja, o cliente escolheu outra opcao (ativo=false). Nao
    # mexe em 'atendente': quem assume/devolve e o painel. Nos demais turnos a
    # WHERE nao casa e e no-op. onError continua: a resposta ja saiu.
    {"parameters": {"operation": "executeQuery",
                    "query": ("INSERT INTO wa_humano (phone, ativo, atualizado_em)\n"
                              "SELECT $1, ($2 = 'human_handoff'), now()\n"
                              "WHERE $2 = 'human_handoff' OR $3 = 'human_handoff'\n"
                              "ON CONFLICT (phone) DO UPDATE\n"
                              "  SET ativo = ($2 = 'human_handoff'), atualizado_em = now();"),
                    "options": {"queryReplacement":
                        "={{ [$('Preparar Persistencia').first().json.phone, "
                        "$('Preparar Persistencia').first().json.step, "
                        "$('Parse & Route').first().json.step] }}"}},
     "id": "pg-marcar-humano", "name": "Marcar Humano", "type": "n8n-nodes-base.postgres",
     "onError": "continueRegularOutput", "alwaysOutputData": True,
     "typeVersion": 2.4, "position": [2650, 280], "credentials": PG_CRED},

    {"parameters": {
        "method": "POST",
        "url": "={{ $env.EVOLUTION_API_URL }}/message/sendText/{{ $env.EVOLUTION_INSTANCE }}",
        "sendBody": True, "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify({ number: $('Preparar Persistencia').first().json.phone, text: $('Preparar Persistencia').first().json.reply_text }) }}",
        "sendHeaders": True,
        "headerParameters": {"parameters": [{"name": "apikey", "value": "={{ $env.EVOLUTION_API_KEY }}"}]},
        "options": {"timeout": 20000}},
     "id": "http-send", "name": "Evolution - Enviar Resposta",
     "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [2850, 0]},
]


def to(name):
    return [{"node": name, "type": "main", "index": 0}]


PERSIST = "Preparar Persistencia"
connections = {
    "Webhook Evolution API": {"main": [to("Extract Inbound")]},
    "Extract Inbound": {"main": [to("Get Session")]},
    "Get Session": {"main": [to("Em atendimento humano?")]},
    "Em atendimento humano?": {"main": [
        to("Registrar Entrada Humano"),  # true: bot cala, so registra a mensagem
        to("Parse & Route"),             # false: fluxo normal do bot
    ]},
    "Parse & Route": {"main": [to("Precisa chamar o SGP?")]},
    "Precisa chamar o SGP?": {"main": [
        to("SGP - Consultar Cliente"),   # lookup_cpf
        to("SGP - Definir Wifi"),        # definir_wifi
        to("GenieACS - Buscar Device"),  # definir_wifi_acs
        to("SGP - ONU do Contrato"),     # definir_wifi_olt
        to("SGP - Abrir Chamado"),       # abrir_chamado
        to("SGP - Segunda Via"),         # segunda_via
        to("SGP - Buscar ONU"),          # diagnostico (identidade ja validada)
        to("SGP - Promessas"),           # promessa (regularizar)
        to(PERSIST),                     # fallback: so responder
    ]},
    "SGP - Consultar Cliente": {"main": [to("Processar Consulta CPF")]},
    "Processar Consulta CPF": {"main": [to("Mais alguma chamada?")]},
    "Mais alguma chamada?": {"main": [
        to("SGP - Segunda Via"),         # identidade ok + intent financeiro
        to("SGP - Buscar ONU"),          # identidade ok + intent diagnostico
        to(PERSIST),                     # fallback: so responder
    ]},
    "SGP - Buscar ONU": {"main": [to("Processar Busca ONU")]},
    "Processar Busca ONU": {"main": [to("Achou a ONU?")]},
    "Achou a ONU?": {"main": [
        to("SGP - ONU Detalhe"),         # true
        to("ONU Nao Encontrada"),        # false
    ]},
    "SGP - ONU Detalhe": {"main": [to("SGP - ONU Info")]},
    "SGP - ONU Info": {"main": [to("Processar Diagnostico")]},
    "Processar Diagnostico": {"main": [to(PERSIST)]},
    "ONU Nao Encontrada": {"main": [to(PERSIST)]},
    "SGP - Segunda Via": {"main": [to("Processar Segunda Via")]},
    "SGP - Promessas": {"main": [to("Processar Promessa")]},
    "Processar Promessa": {"main": [to(PERSIST)]},
    "Processar Segunda Via": {"main": [to(PERSIST)]},
    "SGP - Definir Wifi": {"main": [to("Processar Definir Wifi")]},
    "GenieACS - Buscar Device": {"main": [to("Montar Tarefa Wifi")]},
    "SGP - ONU do Contrato": {"main": [to("Montar Troca na OLT")]},
    "Montar Troca na OLT": {"main": [to("Rota do Wi-Fi")]},
    "Rota do Wi-Fi": {"main": [
        to("OLT - Trocar Wifi"),         # olt: ZTE, sei em que porta escrever
        to("GenieACS - Buscar Device"),  # acs: Huawei no modo auto
        to("Wifi Vira Chamado"),         # chamado: nao achei / sem dado
        to("Wifi Vira Chamado"),         # fallback: valor inesperado -> chamado,
                                         # nunca um item sumindo em silencio
    ]},
    "OLT - Trocar Wifi": {"main": [to("Aplicou na OLT?")]},
    "Aplicou na OLT?": {"main": [
        to("Processar Definir Wifi"),    # true: aplicado, avisa o cliente
        to("Wifi Vira Chamado"),         # false: a OLT recusou
    ]},
    # O pedido nao se perde: vira ocorrencia no SGP com identidade ja validada,
    # exatamente como no modo 'chamado'.
    "Wifi Vira Chamado": {"main": [to("SGP - Abrir Chamado")]},
    "Montar Tarefa Wifi": {"main": [to("Rota do ACS")]},
    "Rota do ACS": {"main": [
        to("GenieACS - Aplicar Wifi"),   # aplicar: achou o device
        to("Wifi Vira Chamado"),         # chamado: Huawei sem TR-069 (modo auto)
        to("ACS Nao Aplicou"),           # extra/handoff: erro real do ACS
    ]},
    "GenieACS - Aplicar Wifi": {"main": [to("Processar Definir Wifi")]},
    "ACS Nao Aplicou": {"main": [to(PERSIST)]},
    "Processar Definir Wifi": {"main": [to(PERSIST)]},
    "SGP - Abrir Chamado": {"main": [to("Processar Chamado")]},
    "Processar Chamado": {"main": [to(PERSIST)]},
    PERSIST: {"main": [to("Upsert Session")]},
    # O registro roda EM SERIE, antes do envio, e cada no continua em erro
    # (onError). Assim ele sempre acontece - independente de o envio dar certo -
    # e, mesmo se falhar, a resposta ao cliente segue. Em paralelo nao servia: o
    # envio a um numero invalido aborta a execucao antes dos ramos paralelos.
    "Upsert Session": {"main": [to("Registrar Mensagens")]},
    "Registrar Mensagens": {"main": [to("Marcar Humano")]},
    "Marcar Humano": {"main": [to("Tem auditoria?")]},
    "Tem auditoria?": {"main": [to("Gravar Auditoria"), to("Evolution - Enviar Resposta")]},
    "Gravar Auditoria": {"main": [to("Evolution - Enviar Resposta")]},
}

wf = {"name": "WhatsApp Autoatendimento ISP (Evolution API + SGP)",
      "nodes": nodes, "connections": connections, "active": False,
      "settings": {"executionOrder": "v1"}, "pinData": {}}

out = "n8n/workflow-wifi-selfservice.json"
with open(out, "w", encoding="utf-8") as f:
    json.dump(wf, f, ensure_ascii=False, indent=2)
print("gerado:", out, "| nodes:", len(nodes))
