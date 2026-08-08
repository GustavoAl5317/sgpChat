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
//   acs     aplica direto pelo cpemanage. Exige ACS configurado.
//   chamado coleta o que o cliente quer e abre uma ocorrencia no SGP para a
//           equipe aplicar. Nao automatiza, mas resolve HOJE: do lado do
//           cliente o atendimento e o mesmo, e a equipe recebe um pedido
//           estruturado em vez de uma ligacao.
//   off     tira a opcao do menu.
//
// O modo 'chamado' existe porque a alternativa real nao era "esperar o ACS", e
// sim o cliente ligar para o suporte - o que ja acontece, so que sem registro.
const WIFI_MODO = String($env.WIFI_MODO || 'acs').trim().toLowerCase();
const WIFI_ON = WIFI_MODO !== 'off';

const MENU = 'Olá! Sou o atendimento automático.\n\n' +
  (WIFI_ON ? '*1* - Alterar nome/senha do Wi-Fi\n' : '') +
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

// Depois que a identidade e confirmada, para onde vai depende do que o
// cliente escolheu no menu. Centralizado aqui para os tres modulos usarem
// exatamente a mesma validacao.
function aposIdentidade(intent, s) {
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
             sgp_payload: { contrato: s.contrato, mac: s.mac || '' } };
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

switch (step) {
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
      reply_text = 'Certo! Vou te transferir para um atendente humano. Aguarde um momento.';
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
      session_patch = { contrato: esc.contrato, contract_options: undefined };
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
    // SSID: 1-32 chars, sem caracteres de controle
    if (text.length < 1 || text.length > 32) {
      reply_text = 'O nome da rede deve ter entre 1 e 32 caracteres. Envie novamente:';
      next_step = 'awaiting_ssid';
    } else if (/[\x00-\x1f]/.test(text)) {
      reply_text = 'O nome da rede tem caracteres inválidos. Envie novamente:';
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
    // WPA2/WPA3-PSK exige 8-63 caracteres ASCII imprimiveis
    if (text.length < 8 || text.length > 63) {
      reply_text = 'A senha precisa ter entre 8 e 63 caracteres. Envie novamente:';
      next_step = 'awaiting_password';
    } else if (!/^[\x20-\x7e]+$/.test(text)) {
      reply_text = 'A senha só pode ter letras, números e símbolos comuns (sem acentos/emoji). Envie novamente:';
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
      const alvo = session.wifi_alvo || 'ambos';
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

  case 'human_handoff': {
    reply_text = 'Você está na fila de atendimento humano. Em breve alguém falará com você por aqui.\n\nDigite *menu* para voltar ao início.';
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

function aposIdentidade(it, contrato, mac, ssidAtual) {
  if (it === 'financeiro') {
    return { sgp_action: 'segunda_via', next_step: 'menu', reply_text: null,
             sgp_payload: { contrato: contrato } };
  }
  if (it === 'diagnostico') {
    return { sgp_action: 'diagnostico', next_step: 'menu', reply_text: null,
             sgp_payload: { contrato: contrato, mac: mac || '' } };
  }
  if (it === 'suporte') {
    return { sgp_action: 'none', next_step: 'awaiting_support_desc', sgp_payload: {},
             reply_text: 'Descreva o problema que você está enfrentando (em uma mensagem):' };
  }
  return { sgp_action: 'none', next_step: 'awaiting_wifi_what', sgp_payload: {},
           reply_text: promptAlvoWifi(ssidAtual) };
}

// Resposta do SGP: { msg, contratos: [ ... ] }
const contratos = Array.isArray(resp && resp.contratos) ? resp.contratos : [];
// contratoStatus: 1=Ativo, 2=Inativo, 4=Suspenso
const ativos = contratos.filter(function (c) { return c.contratoStatus === 1; });

if (contratos.length === 0) {
  reply_text = 'Não encontrei nenhum contrato com esse CPF/CNPJ. Confira o número ou digite *5* para falar com um atendente.';
  next_step = 'menu';
} else if (ativos.length === 0) {
  reply_text = 'Localizei seu cadastro, mas não há contrato ativo no momento. Vou te transferir para um atendente.';
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
  // servico_mac casa com o phy_addr da ONU - e o plano B para achar o
  // equipamento quando o filtro por contrato nao retorna nada.
  session_patch.mac = ref.servico_mac || ref.servico_mac2 || '';
  // Nome atual da rede: usado para o cliente reconhecer de qual Wi-Fi
  // estamos falando. Pode vir vazio - a base nem sempre tem esse campo.
  session_patch.wifi_ssid_atual = ref.servico_wifi_ssid || '';

  if (ativos.length === 1) {
    session_patch.contrato = ref.contratoId;
  } else {
    // Cliente com mais de um contrato ativo: precisa escolher qual
    session_patch.contract_options = ativos.slice(0, 9).map(function (c) {
      return { contrato: c.contratoId,
               label: (c.servico_plano || c.planointernet || 'Plano') + ' - ' +
                      (c.endereco_logradouro || '') + ' ' + (c.endereco_numero || '') };
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
                               session_patch.wifi_ssid_atual);
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

// Resposta do SGP: { msg, success }
const sucesso = !!(resp && resp.success === true);
const ssidNovo = prev.sgp_payload && prev.sgp_payload.ssid;
const senhaNova = prev.sgp_payload && prev.sgp_payload.senha;

if (sucesso) {
  // Confirma exatamente o que mudou. Dizer "nome e senha atualizados" para
  // quem so trocou a senha faz o cliente procurar uma rede que nao existe.
  reply_text = 'Pronto! Sua rede Wi-Fi foi atualizada:\n';
  if (ssidNovo) reply_text += '\n*Nome:* ' + ssidNovo;
  if (senhaNova) reply_text += '\n*Senha:* alterada';
  reply_text += '\n\nO roteador pode levar alguns minutos para aplicar. ';
  reply_text += senhaNova
    ? 'Seus aparelhos vão precisar conectar de novo com a nova senha.'
    : 'Seus aparelhos devem reconectar sozinhos com a senha de sempre.';
  reply_text += '\n\nDigite *menu* se precisar de mais alguma coisa.';
  next_step = 'menu';
  session_patch.reset = true;
} else {
  const msg = (resp && resp.msg) ? String(resp.msg) : '';
  if (/Gerenciador de CPE/i.test(msg)) {
    reply_text = 'Seu roteador não está habilitado para configuração remota. Vou te transferir para um atendente resolver isso.';
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
    resposta_sgp: resp,
  },
}) }];
"""

# ---------------------------------------------------------------- Financeiro
JS_PROC_FATURA = r"""
const prev = $('Parse & Route').first().json;
const resp = $input.first().json;

// Resposta do SGP: { status, razaoSocial, links: [ {fatura, vencimento, valor,
// valor_original, linhadigitavel, link, link_cobranca, juros, multa} ] }
const links = Array.isArray(resp && resp.links) ? resp.links : [];

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
    if (f.linhadigitavel) t += '\n*Linha digitável:*\n`' + f.linhadigitavel + '`';
    if (f.link) t += '\n' + f.link;
    return t;
  });

  reply_text = (links.length > 3
      ? 'Você tem *' + links.length + '* faturas em aberto. Mostrando as ' + mostrar.length + ' mais antigas:\n\n'
      : (links.length === 1 ? 'Aqui está sua fatura em aberto:\n\n'
                            : 'Você tem *' + links.length + '* faturas em aberto:\n\n'))
    + blocos.join('\n\n---\n\n')
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

if (protocolo) {
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
    tipo: 'chamado',
    phone: prev.phone,
    cpf: prev.session.cpf,
    contrato: prev.sgp_payload.contrato,
    ssid_novo: protocolo ? ('protocolo:' + protocolo) : null,
    sucesso: !!protocolo,
    resposta_sgp: resp,
  },
}) }];
"""

# ---------------------------------------------------------------- Diagnostico
JS_PROC_BUSCA_ONU = r"""
const prev = $('Parse & Route').first().json;
const lista = $input.first().json;

// /api/fttx/onu/list/ devolve um array. Filtrar por ?contrato= e o caminho
// natural, mas nem toda base tem esse vinculo preenchido - por isso o node
// seguinte tenta de novo por phy_addr (que casa com servico_mac do contrato).
const onus = Array.isArray(lista) ? lista : [];
const mac = String((prev.sgp_payload && prev.sgp_payload.mac) || '').replace(/[^a-zA-Z0-9]/g, '').toLowerCase();

let escolhida = null;
if (onus.length === 1) {
  escolhida = onus[0];
} else if (onus.length > 1 && mac) {
  escolhida = onus.find(function (o) {
    return String(o.phy_addr || '').replace(/[^a-zA-Z0-9]/g, '').toLowerCase() === mac;
  }) || onus[0];
} else if (onus.length > 1) {
  escolhida = onus[0];
}

return [{ json: Object.assign({}, prev, {
  onu_id: escolhida ? escolhida.id : null,
  onu_basica: escolhida || null,
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

function classificar(dbm) {
  if (dbm === null) return null;
  if (dbm >= -25) return { rotulo: 'Bom', nota: 'Seu sinal está dentro do esperado.' };
  if (dbm >= -27) return { rotulo: 'Atenção', nota: 'Sinal no limite. Pode oscilar em dias de chuva.' };
  return { rotulo: 'Ruim', nota: 'Sinal abaixo do recomendado - precisa de visita técnica.' };
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

const linhas = [];
if (base.type || onu.modelo) linhas.push('*Equipamento:* ' + (onu.modelo || base.type));
if (onu.cto) linhas.push('*Caixa (CTO):* ' + onu.cto + (onu.porta_cto ? ' / porta ' + onu.porta_cto : ''));
if (dbm !== null) {
  let l = '*Sinal óptico:* ' + dbm.toFixed(2) + ' dBm  (' + cls.rotulo + ')';
  if (md) l += '\n_medido em ' + md[3] + '/' + md[2] + ' às ' + md[4] + ':' + md[5] + '_';
  linhas.push(l);
}

let reply_text;
if (!linhas.length) {
  reply_text = 'Não consegui ler os dados do seu equipamento agora. ' +
    'Digite *3* para abrir um chamado ou *5* para falar com um atendente.';
} else {
  reply_text = 'Diagnóstico da sua conexão:\n\n' + linhas.join('\n');
  if (cls) reply_text += '\n\n' + cls.nota;
  if (dbm === null) {
    reply_text += '\n\nNão consegui medir o sinal óptico neste momento.';
  } else if (horasAtras !== null && horasAtras > 48) {
    reply_text += '\n\n_Obs.: essa é a última leitura registrada, não uma medição ' +
      'de agora. Se o problema começou depois disso, digite *3* para abrir um chamado._';
  }
  if (cls && cls.rotulo === 'Ruim') {
    reply_text += '\n\nDigite *3* para abrir um chamado técnico.';
  }
  reply_text += '\n\nDigite *menu* para voltar ao início.';
}

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
    sucesso: linhas.length > 0,
    resposta_sgp: { onu_id: prev.onu_id, cto: onu.cto || null, sinal_dbm: dbm,
                    sinal_origem: origem, medido_em: medidoEm || null },
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

return [{
  json: {
    phone: item.phone,
    step: item.next_step,
    data: JSON.stringify(merged),
    reply_text: texto,
    audit: item._audit ? JSON.stringify(item._audit) : null,
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
                              "SELECT step, data FROM wa_sessions\n"
                              " WHERE phone = $1 AND updated_at >= now() - interval '30 minutes'\n"
                              "UNION ALL\n"
                              "SELECT NULL, NULL WHERE NOT EXISTS (\n"
                              "    SELECT 1 FROM wa_sessions\n"
                              "     WHERE phone = $1 AND updated_at >= now() - interval '30 minutes')"),
                    "options": {"queryReplacement": "={{ [$json.phone] }}"}},
     "id": "pg-get", "name": "Get Session", "type": "n8n-nodes-base.postgres",
     # O UNION ALL garante que o node devolva 1 item mesmo se o cliente for novo,
     # impedindo que o n8n aborte silenciosamente o fluxo aqui por falta de dados.
     "alwaysOutputData": True,
     "typeVersion": 2.4, "position": [400, 0], "credentials": PG_CRED},

    code_node("code-route", "Parse & Route", JS_PARSE_ROUTE, [600, 0]),

    # Switch principal: o que a mensagem do cliente disparou
    {"parameters": {"rules": {"values": [
        {"conditions": cond("={{ $json.sgp_action }}", "lookup_cpf"),
         "renameOutput": True, "outputKey": "lookup_cpf"},
        {"conditions": cond("={{ $json.sgp_action }}", "definir_wifi"),
         "renameOutput": True, "outputKey": "definir_wifi"},
        {"conditions": cond("={{ $json.sgp_action }}", "abrir_chamado"),
         "renameOutput": True, "outputKey": "abrir_chamado"},
        {"conditions": cond("={{ $json.sgp_action }}", "segunda_via"),
         "renameOutput": True, "outputKey": "segunda_via"},
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

    code_node("code-proc-wifi", "Processar Definir Wifi", JS_PROC_WIFI, [1200, 0]),

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
    "Get Session": {"main": [to("Parse & Route")]},
    "Parse & Route": {"main": [to("Precisa chamar o SGP?")]},
    "Precisa chamar o SGP?": {"main": [
        to("SGP - Consultar Cliente"),   # lookup_cpf
        to("SGP - Definir Wifi"),        # definir_wifi
        to("SGP - Abrir Chamado"),       # abrir_chamado
        to("SGP - Segunda Via"),         # segunda_via
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
    "Processar Segunda Via": {"main": [to(PERSIST)]},
    "SGP - Definir Wifi": {"main": [to("Processar Definir Wifi")]},
    "Processar Definir Wifi": {"main": [to(PERSIST)]},
    "SGP - Abrir Chamado": {"main": [to("Processar Chamado")]},
    "Processar Chamado": {"main": [to(PERSIST)]},
    PERSIST: {"main": [to("Upsert Session")]},
    "Upsert Session": {"main": [to("Tem auditoria?")]},
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
