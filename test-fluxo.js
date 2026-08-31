// Roda os Code nodes do workflow fora do n8n, usando RESPOSTAS REAIS capturadas
// da API demo do SGP. Cobre os tres modulos: Wi-Fi, financeiro e suporte.
//
// Uso: node test-fluxo.js <resposta-consultacliente.json>
const fs = require('fs');

const wf = JSON.parse(fs.readFileSync('n8n/workflow-wifi-selfservice.json', 'utf8'));
const code = {};
wf.nodes.forEach(n => { if (n.parameters && n.parameters.jsCode) code[n.name] = n.parameters.jsCode; });

// Os Code nodes leem configuracao de $env (o compose libera com
// N8N_BLOCK_ENV_ACCESS_IN_NODE=false). Aqui vale o default de producao, e cada
// teste sobrescreve o que precisa.
let ENV = {};

function run(nodeName, inputs, refs) {
  const fn = new Function('$input', '$', '$env', code[nodeName]);
  const $input = { first: () => ({ json: inputs[0] }), all: () => inputs.map(j => ({ json: j })) };
  const $ = (r) => ({ first: () => ({ json: refs[r] }) });
  const out = fn($input, $, ENV);
  return out.length ? out[0].json : null;
}

// Sem argumento usa o fixture versionado (estrutura real, dados ficticios).
const RESP = JSON.parse(fs.readFileSync(
  process.argv[2] || 'fixtures/consultacliente-exemplo.json', 'utf8'));

// A base demo cadastra os clientes com o CPF placeholder 000.000.000-00, que
// (corretamente) nao passa na validacao de digito verificador. Para exercitar o
// fluxo mantendo a ESTRUTURA real da resposta, injetamos um CPF valido.
const CPF = '52998224725';
RESP.contratos.forEach(c => { c.cpfCnpj = '529.982.247-25'; });
const CONTRATOS = RESP.contratos;
const ativos = CONTRATOS.filter(c => c.contratoStatus === 1);

// Formato real capturado da Evolution v2.3.7: o n8n envelopa o corpo da
// requisicao em `body`, e o payload da Evolution fica dentro dele.
function payload(text, phone, extra) {
  return { body: Object.assign({
    event: 'messages.upsert',
    instance: 'principal',
    data: {
      key: { remoteJid: phone + '@s.whatsapp.net', fromMe: false, id: 'ABC123' },
      pushName: 'Fulano',
      message: { conversation: text },
      messageType: 'conversation',
      source: 'ios',
    },
  }, extra || {}) };
}

function inbound(text, phone) {
  return run('Extract Inbound', [payload(text, phone)], {});
}

// Um turno completo: mensagem do cliente -> resposta do bot + nova sessao.
// `sgpResponse` e a resposta da chamada que o turno dispara; `faturas` so e
// usada quando o turno encadeia consulta de cliente + busca de faturas.
// `espiar` recebe o sgp_payload montado, para os testes conferirem o corpo que
// sairia para o SGP - o node de HTTP nao roda aqui, entao e o unico ponto onde
// da para verificar que campo nenhum viaja vazio.
function turn(sessionRow, text, phone, sgpResponse, faturas, diag, espiar, acs, olt) {
  let montado = null;
  const inb = inbound(text, phone);
  if (!inb) return { skipped: true, sessionRow };
  let r = run('Parse & Route', sessionRow ? [sessionRow] : [], { 'Extract Inbound': inb });
  if (espiar) espiar(r.sgp_payload || {});

  if (r.sgp_action === 'lookup_cpf') {
    r = run('Processar Consulta CPF', [sgpResponse], { 'Parse & Route': r });
    // Switch "Buscar faturas agora?": financeiro precisa de mais uma chamada
    if (r.sgp_action === 'segunda_via') {
      r = run('Processar Segunda Via', [faturas], { 'Parse & Route': r });
    }
  } else if (r.sgp_action === 'definir_wifi') {
    r = run('Processar Definir Wifi', [sgpResponse], { 'Parse & Route': r });
  } else if (r.sgp_action === 'segunda_via') {
    // Veio do switch principal (ex: apos escolher o contrato)
    r = run('Processar Segunda Via', [faturas || sgpResponse], { 'Parse & Route': r });
  } else if (r.sgp_action === 'abrir_chamado') {
    r = run('Processar Chamado', [sgpResponse], { 'Parse & Route': r });
  } else if (r.sgp_action === 'definir_wifi_olt') {
    // Caminho da OLT: achar a ONU no SGP -> montar o endereco -> aplicar.
    montado = run('Montar Troca na OLT', [(olt && olt.lista) || []], { 'Parse & Route': r });
    if (!montado.olt_onu) {
      r = run('OLT Nao Aplicou', [montado], {});
    } else {
      r = run('Processar Definir Wifi',
              [(olt && olt.aplicar) || { statusCode: 200, body: { ok: true } }],
              { 'Parse & Route': r, 'Montar Troca na OLT': montado });
    }
  } else if (r.sgp_action === 'definir_wifi_acs') {
    // Caminho da NBI: buscar o device -> montar a tarefa -> aplicar. O IF
    // "Da para aplicar no ACS?" e reproduzido aqui pelo teste de acs_device_id.
    montado = run('Montar Tarefa Wifi', [(acs && acs.busca) || { statusCode: 200, body: [] }],
                  { 'Parse & Route': r });
    if (!montado.acs_device_id) {
      r = run('ACS Nao Aplicou', [montado], {});
    } else {
      r = run('Processar Definir Wifi', [(acs && acs.aplicar) || { statusCode: 200, body: {} }],
              { 'Parse & Route': r, 'Montar Tarefa Wifi': montado });
    }
  }

  // Diagnostico encadeia: buscar ONU -> detalhe -> info
  if (r.sgp_action === 'diagnostico' && diag) {
    const busca = run('Processar Busca ONU', [diag.lista], { 'Parse & Route': r });
    if (busca.onu_id == null) {
      r = run('ONU Nao Encontrada', [busca], {});
    } else {
      r = run('Processar Diagnostico', [diag.info],
              { 'Processar Busca ONU': busca, 'SGP - ONU Detalhe': diag.detalhe });
    }
  }

  const p = run('Preparar Persistencia', [r], {});
  return { reply: p.reply_text, step: p.step, sessionRow: { step: p.step, data: p.data },
           data: JSON.parse(p.data), audit: p.audit ? JSON.parse(p.audit) : null,
           montado: montado };
}

let ok = 0, fail = 0;
function check(c, m) { if (c) { console.log('  OK    ' + m); ok++; } else { console.log('  FALHA ' + m); fail++; } }

const PHONE_OK = '55' + ativos[0].telefones[0].contato.replace(/\D/g, '');
const PHONE_OUTRO = '5599911112222';

// Resposta real do fatura2via da base demo (contrato 83, 40 titulos em aberto)
const FATURAS = { status: 1, razaoSocial: 'ZE DO ALHO', links: [
  { fatura: 9558, vencimento: '2026-08-05', vencimento_original: '2024-10-15', valor: 9.91,
    valor_original: 7.99, juros: 1.76, multa: 0.16,
    linhadigitavel: '23795.78400 40000.000121 41076.449002 7 15290000000991',
    link: 'https://demo.sgp.net.br/boleto/9558-7WVYNOLW4T/' },
  { fatura: 9559, vencimento: '2024-11-15', valor: 7.99, valor_original: 7.99, juros: 0, multa: 0,
    linhadigitavel: '23795.78400 40000.000121 41076.449002 7 15290000000992',
    link: 'https://demo.sgp.net.br/boleto/9559-XXXX/' },
]};
const SEM_FATURA = { status: 0, razaoSocial: 'PEDRO', links: [] };

function ateIdentidade(opcaoMenu, phone, diag) {
  let s = null;
  let t = turn(s, opcaoMenu, phone); s = t.sessionRow;
  t = turn(s, CPF, phone, RESP, FATURAS, diag); s = t.sessionRow;
  if (t.step === 'awaiting_contract_choice') {
    t = turn(s, '1', phone, RESP, FATURAS, diag); s = t.sessionRow;
  }
  return { t, s };
}

console.log('Base demo: ' + CONTRATOS.length + ' contratos, ' + ativos.length + ' ativos');

// ============================ MODULO 1: Wi-Fi ============================
console.log('\n=== Modulo 1: Wi-Fi ===');
let { t, s } = ateIdentidade('1', PHONE_OK);
check(t.step === 'awaiting_wifi_what', 'menu 1 + identidade -> pergunta o que alterar');
check(/nome/i.test(t.reply) && /senha/i.test(t.reply), 'oferece nome, senha ou os dois');

t = turn(s, 'sei la', PHONE_OK);
check(t.step === 'awaiting_wifi_what', 'resposta fora de 1/2/3 repete a pergunta');

t = turn(s, '3', PHONE_OK); s = t.sessionRow;
check(t.step === 'awaiting_ssid', 'opcao 3 (nome e senha) -> pede o nome');
t = turn(s, 'Wifi da Familia', PHONE_OK); s = t.sessionRow;
check(t.step === 'awaiting_password', 'SSID aceito -> pede senha');
t = turn(s, 'abc', PHONE_OK); s = t.sessionRow;
check(t.step === 'awaiting_password', 'senha curta rejeitada');

// O cpemanage escreve no roteador em toda chamada - nada e aplicado sem o
// cliente confirmar, e o aviso da queda dos aparelhos vem antes.
t = turn(s, 'MinhaSenha123', PHONE_OK); s = t.sessionRow;
check(t.step === 'awaiting_wifi_confirm', 'senha valida -> pede confirmacao');
check(/desconectar/.test(t.reply), 'confirmacao avisa que os aparelhos vao cair');
check(/Wifi da Familia/.test(t.reply) && /MinhaSenha123/.test(t.reply),
      'confirmacao mostra nome e senha escolhidos');

t = turn(s, 'talvez', PHONE_OK); s = t.sessionRow;
check(t.step === 'awaiting_wifi_confirm', 'resposta ambigua nao aplica nada');

t = turn(s, '2', PHONE_OK); s = t.sessionRow;
check(t.step === 'menu' && !t.audit, 'cancelar nao chama o SGP nem audita');

// refaz ate a confirmacao para exercitar o caminho de sucesso
({ t, s } = ateIdentidade('1', PHONE_OK));
t = turn(s, '3', PHONE_OK); s = t.sessionRow;
t = turn(s, 'Wifi da Familia', PHONE_OK); s = t.sessionRow;
t = turn(s, 'MinhaSenha123', PHONE_OK); s = t.sessionRow;
t = turn(s, '1', PHONE_OK, { msg: 'Alteracoes realizadas com sucesso.', success: true });
check(t.step === 'menu', 'sucesso -> volta ao menu');
check(Object.keys(t.data).length === 0, 'sessao limpa apos concluir');
check(t.audit && t.audit.tipo === 'wifi', 'auditoria tipo=wifi');
check(!/MinhaSenha123/.test(JSON.stringify(t.data)), 'senha nao fica guardada na sessao');

// ---- So o nome: nao pede senha, e nao manda nada de senha ao SGP ----
// Campo vazio no cpemanage APAGA - entao o corpo tem que sair sem nova_senha.
({ t, s } = ateIdentidade('1', PHONE_OK));
t = turn(s, '1', PHONE_OK); s = t.sessionRow;
check(t.step === 'awaiting_ssid', 'opcao 1 (so nome) -> pede o nome');
t = turn(s, 'RedeNova', PHONE_OK); s = t.sessionRow;
check(t.step === 'awaiting_wifi_confirm', 'so nome -> vai direto a confirmacao, sem pedir senha');
check(!/senha nova/i.test(t.reply), 'so nome -> nao ameaca trocar a senha');
let corpo = null;
t = turn(s, '1', PHONE_OK, { msg: 'ok', success: true }, null, null,
         (p) => { corpo = p.form; });
check(/novo_ssid=RedeNova/.test(corpo || ''), 'so nome -> corpo leva novo_ssid');
check(!/nova_senha/.test(corpo || ''), 'so nome -> corpo NAO leva nova_senha');
check(!/Senha/.test(t.reply), 'so nome -> sucesso nao fala de senha alterada');

// ---- So a senha: nem pergunta o nome, e nao manda novo_ssid ----
({ t, s } = ateIdentidade('1', PHONE_OK));
t = turn(s, '2', PHONE_OK); s = t.sessionRow;
check(t.step === 'awaiting_password', 'opcao 2 (so senha) -> pede a senha direto');
t = turn(s, 'OutraSenha99', PHONE_OK); s = t.sessionRow;
check(t.step === 'awaiting_wifi_confirm', 'so senha -> confirmacao');
check(!/Novo nome/.test(t.reply), 'so senha -> confirmacao nao inventa nome novo');
corpo = null;
t = turn(s, '1', PHONE_OK, { msg: 'ok', success: true }, null, null,
         (p) => { corpo = p.form; });
check(/nova_senha=OutraSenha99/.test(corpo || ''), 'so senha -> corpo leva nova_senha');
check(!/novo_ssid/.test(corpo || ''), 'so senha -> corpo NAO leva novo_ssid (apagaria a rede)');

// roteador sem ACS (resposta real da demo)
t = turn({ step: 'awaiting_wifi_confirm',
           data: JSON.stringify({ contrato: 566, ssid_new: 'X', senha_new: 'SenhaValida123' }) },
         '1', PHONE_OK,
         { msg: 'O Servico de internet nao possui Gerenciador de CPE configurado.', success: false });
check(t.step === 'human_handoff', 'roteador sem CPE -> atendente');

// ========================= MODULO 2: Financeiro =========================
// ================= Identidade reaproveitada (janela de 15 min) =================
// O cliente resolve duas coisas na mesma conversa. Repetir CPF + data de
// nascimento a cada modulo e o motivo mais comum de ninguem terminar o
// atendimento - mas a janela precisa expirar, senao vira porta aberta.
console.log('\n=== Identidade reaproveitada ===');

// Sessao de quem acabou de provar quem e, com o contrato ja resolvido.
function sessaoValidada(idadeMs, extra) {
  return { step: 'menu', data: JSON.stringify(Object.assign({
    contrato: ativos[0].contratoId, cpf: '12345678909', intent: 'wifi',
    mac: ativos[0].servico_mac || '', verified_at: Date.now() - (idadeMs || 0),
  }, extra || {})) };
}

// Sem passar CPF de novo: vai direto ao boleto.
t = turn(sessaoValidada(60 * 1000), '2', PHONE_OK, null, FATURAS);
check(!/CPF/i.test(t.reply || ''), 'identidade recente -> nao pede CPF de novo');
check(/Vencimento/.test(t.reply || ''), 'identidade recente -> ja mostra as faturas');

t = turn(sessaoValidada(60 * 1000), '1', PHONE_OK);
check(t.step === 'awaiting_wifi_what', 'identidade recente -> Wi-Fi vai direto ao que alterar');
check(t.data.ident_reaproveitada === true, 'auditoria sabe que a identidade foi reaproveitada');

// Passados os 15 min, revalida do zero.
t = turn(sessaoValidada(16 * 60 * 1000), '2', PHONE_OK, null, FATURAS);
check(t.step === 'awaiting_cpf', 'identidade expirada -> pede CPF de novo');

// Sessao sem verified_at (versao antiga do fluxo, ou nunca validada).
t = turn({ step: 'menu', data: JSON.stringify({ contrato: 566 }) }, '2', PHONE_OK, null, FATURAS);
check(t.step === 'awaiting_cpf', 'contrato na sessao sem validacao nao vale identidade');

// verified_at no futuro (relogio torto) nao pode virar sessao eterna.
t = turn(sessaoValidada(-60 * 60 * 1000), '2', PHONE_OK, null, FATURAS);
check(t.step === 'awaiting_cpf', 'verified_at no futuro nao e aceito');

// "menu"/"sair" reinicia: o reset limpa a identidade junto. Vale tambem
// estando ja no menu - e o unico jeito do cliente encerrar de proposito.
let sv = sessaoValidada(60 * 1000); sv.step = 'awaiting_ssid';
t = turn(sv, 'menu', PHONE_OK);
check(Object.keys(t.data).length === 0, 'digitar menu no meio do fluxo descarta a identidade');
t = turn(sessaoValidada(60 * 1000), 'sair', PHONE_OK);
check(Object.keys(t.data).length === 0, 'digitar sair no menu encerra a sessao autenticada');

// ---- Nome atual da rede no prompt do Wi-Fi ----
// O cliente nao sabe o que e "SSID" e nao lembra o nome da propria rede.
t = turn(sessaoValidada(60 * 1000, { wifi_ssid_atual: 'RCNET-CASA' }), '1', PHONE_OK);
check(/RCNET-CASA/.test(t.reply || ''), 'mostra o nome atual da rede quando o SGP tem');
check(!/SSID/.test(t.reply || ''), 'nao usa o jargao SSID com o cliente');
// Sem nome cadastrado o bot nao tem ancora para mostrar, mas segue perguntando
// o que alterar - e so ao pedir o nome novo e que explica onde encontrar.
let semNome = turn(sessaoValidada(60 * 1000, { wifi_ssid_atual: '' }), '1', PHONE_OK);
check(!/SSID/.test(semNome.reply || ''), 'sem nome cadastrado, nao usa jargao');
semNome = turn(semNome.sessionRow, '1', PHONE_OK);
check(/redes Wi-Fi/.test(semNome.reply || ''),
      'sem nome cadastrado, explica onde encontrar');

console.log('\n=== Modulo 2: Financeiro (2a via) ===');
({ t, s } = ateIdentidade('2', PHONE_OK));
console.log('  bot:', JSON.stringify(t.reply).slice(0, 150));
check(t.step === 'menu', 'menu 2 + identidade -> mostra faturas e volta ao menu');
check(/Vencimento/.test(t.reply), 'resposta traz vencimento');
check(/23795/.test(t.reply), 'resposta traz a linha digitavel');
check(/R\$ 9,91/.test(t.reply), 'valor formatado em BRL com juros');
check(/05\/08\/2026/.test(t.reply), 'data convertida de ISO para DD/MM/AAAA');
check(t.audit && t.audit.tipo === 'segunda_via', 'auditoria tipo=segunda_via');
check(!/23795/.test(JSON.stringify(t.audit)), 'linha digitavel NAO vai para o log de auditoria');
check(Object.keys(t.data).length === 0, 'sessao limpa apos mostrar faturas');

// ordena da mais antiga para a mais nova
const posPrimeira = t.reply.indexOf('15/11/2024');
const posSegunda = t.reply.indexOf('05/08/2026');
check(posPrimeira !== -1 && posPrimeira < posSegunda, 'faturas ordenadas: mais antiga primeiro');

// sem faturas em aberto
let s2 = { step: 'awaiting_cpf', data: JSON.stringify({ intent: 'financeiro' }) };
t = turn(s2, CPF, PHONE_OK, RESP, SEM_FATURA);
if (t.step === 'awaiting_contract_choice') t = turn(t.sessionRow, '1', PHONE_OK, RESP, SEM_FATURA);
check(/não tem nenhuma fatura em aberto/i.test(t.reply || ''), 'sem faturas -> mensagem apropriada');

// ========================== MODULO 3: Suporte ==========================
console.log('\n=== Modulo 3: Suporte ===');
({ t, s } = ateIdentidade('3', PHONE_OK));
check(t.step === 'awaiting_support_desc', 'menu 3 + identidade -> pede descricao');
t = turn(s, 'lento', PHONE_OK); s = t.sessionRow;
check(t.step === 'awaiting_support_desc', 'descricao curta rejeitada');
t = turn(s, 'Minha internet esta muito lenta desde ontem a noite', PHONE_OK,
         { status: 1, razaoSocial: 'PEDRO', protocolo: '200429142914', contratoId: 566, msg: '' });
console.log('  bot:', JSON.stringify(t.reply).slice(0, 120));
check(t.step === 'menu', 'chamado aberto -> volta ao menu');
check(/200429142914/.test(t.reply), 'protocolo informado ao cliente');
check(t.audit && t.audit.tipo === 'chamado', 'auditoria tipo=chamado');

t = turn({ step: 'awaiting_support_desc', data: JSON.stringify({ contrato: 566 }) },
         'Descricao suficientemente longa aqui', PHONE_OK, { status: 0, msg: 'erro qualquer' });
check(t.step === 'human_handoff', 'falha ao abrir chamado -> atendente');

// ======================= Seguranca (todos os modulos) =======================
console.log('\n=== Seguranca ===');
for (const [opt, nome] of [['1', 'wifi'], ['2', 'financeiro'], ['3', 'suporte']]) {
  let ss = null;
  let tt = turn(ss, opt, PHONE_OUTRO); ss = tt.sessionRow;
  tt = turn(ss, CPF, PHONE_OUTRO, RESP, FATURAS); ss = tt.sessionRow;
  if (tt.step === 'awaiting_contract_choice') { tt = turn(ss, '1', PHONE_OUTRO, RESP, FATURAS); ss = tt.sessionRow; }
  check(tt.step === 'awaiting_second_factor', 'modulo ' + nome + ': telefone desconhecido exige 2FA');
}

let s3 = null;
t = turn(s3, '1', PHONE_OUTRO); s3 = t.sessionRow;
for (let i = 0; i < 3; i++) { t = turn(s3, '11111111111', PHONE_OUTRO); s3 = t.sessionRow; }
check(t.step === 'human_handoff', '3 CPFs invalidos -> atendente');

s3 = { step: 'awaiting_second_factor', data: JSON.stringify({ attempts: 0, second_factor_target: '1990-05-20', intent: 'financeiro' }) };
for (let i = 0; i < 3; i++) { t = turn(s3, '01/01/1900', PHONE_OUTRO); s3 = t.sessionRow; }
check(t.step === 'human_handoff', '3 erros de 2FA -> atendente');

// 2FA correto libera o modulo escolhido, nao sempre o de wifi
t = turn({ step: 'awaiting_second_factor',
           data: JSON.stringify({ attempts: 0, second_factor_target: '1990-05-20', intent: 'suporte', contrato: 83 }) },
         '20/05/1990', PHONE_OUTRO);
check(t.step === 'awaiting_support_desc', '2FA ok respeita o modulo escolhido (suporte)');

t = turn({ step: 'awaiting_cpf', data: '{}' }, CPF, PHONE_OK, { msg: 'x', contratos: [] });
check(t.step === 'menu', 'CPF sem contrato -> volta ao menu');
t = turn({ step: 'awaiting_cpf', data: '{}' }, CPF, PHONE_OK,
         { msg: 'x', contratos: CONTRATOS.filter(c => c.contratoStatus !== 1) });
check(t.step === 'human_handoff', 'so contratos inativos -> atendente');

console.log('\n=== Mensagens ignoradas ===');
check(inbound('oi', '5511999999999') !== null, 'mensagem privada e processada');
const pg = payload('oi', '123456'); pg.body.data.key.remoteJid = '123456@g.us';
const grupo = run('Extract Inbound', [pg], {});
check(grupo === null, 'mensagem de grupo e ignorada');
const p2 = payload('oi', '5511999999999'); p2.body.data.key.fromMe = true;
const propria = run('Extract Inbound', [p2], {});
check(propria === null, 'mensagem enviada pelo proprio bot e ignorada');

// ======================== MODULO 4: Diagnostico ========================
console.log('\n=== Modulo 4: Diagnostico da conexao ===');

// Estruturas reais capturadas da API demo
const ONU_LISTA = [{ id: 6485, olt_name: 'ONE', slot: 0, pon: 1, onuid: 4,
  type: '5506-04-f1', mode: 'PPPoE', phy_addr: 'TSMX-029ba901' }];
const ONU_DETALHE = { onu: { vlan: 1000, pon: 1, olt: 'ONE', onu: 4, slot: 0,
  addr: 'TSMX-029ba901', tipo: '5506-04-f1', modelo: 'huawei',
  porta_cto: 3, cto: 'CTO-CENTRO-07', descricao: '', modo: 'PPPoE' } };

// Formatos de saida das OLTs mais comuns no Brasil
const OLTS = {
  huawei:    { result: 'ONT  Rx power(dBm)  Tx power(dBm)\n  0/1/1  -19.45  2.31' },
  zte:       { result: 'Rx Power: -22.07 dbm   Tx Power: 2.15 dbm' },
  fiberhome: { result: 'optical power: rx -26.80dBm tx 1.90dBm' },
  ruim:      { result: 'ONT Rx power(dBm): -29.55' },
  // Falha real capturada da demo (OLT inexistente)
  falha:     { result: 'End Of File (EOF). Exception style platform.\ncommand: /usr/bin/ssh\nssh: Could not resolve hostname one' },
};

for (const vendor of Object.keys(OLTS)) {
  const d = { lista: ONU_LISTA, detalhe: ONU_DETALHE, info: OLTS[vendor] };
  const td = ateIdentidade('4', PHONE_OK, d).t;
  const temSinal = /Sinal óptico/.test(td.reply || '');
  if (vendor === 'falha') {
    check(!temSinal && /Não consegui medir o sinal/.test(td.reply),
          'OLT fora do ar -> nao inventa sinal, avisa que nao mediu');
    check(/CTO-CENTRO-07/.test(td.reply), '  ...mas ainda entrega CTO e equipamento');
  } else if (vendor === 'ruim') {
    check(/Ruim/.test(td.reply) && /visita técnica/.test(td.reply),
          'sinal -29.55 dBm -> Ruim + sugere chamado');
  } else {
    check(temSinal, 'OLT ' + vendor + ': sinal extraido');
  }
}

const tdiag = ateIdentidade('4', PHONE_OK,
  { lista: ONU_LISTA, detalhe: ONU_DETALHE, info: OLTS.huawei }).t;
console.log('  bot:', JSON.stringify(tdiag.reply).slice(0, 190));
check(/-19\.45 dBm/.test(tdiag.reply), 'valor do sinal correto na resposta');
check(/Bom/.test(tdiag.reply), '-19.45 dBm classificado como Bom');
check(/CTO-CENTRO-07/.test(tdiag.reply) && /porta 3/.test(tdiag.reply), 'mostra CTO e porta');
check(tdiag.audit && tdiag.audit.tipo === 'diagnostico', 'auditoria tipo=diagnostico');
check(tdiag.step === 'menu' && Object.keys(tdiag.data).length === 0, 'sessao limpa apos diagnostico');

// Sem ONU vinculada (exatamente o caso da base demo)
const tsem = ateIdentidade('4', PHONE_OK,
  { lista: [], detalhe: ONU_DETALHE, info: OLTS.huawei }).t;
check(/Não localizei o equipamento/.test(tsem.reply || ''), 'sem ONU vinculada -> mensagem clara');

// Numeros soltos no texto da OLT nao podem virar leitura de sinal
const tabs = ateIdentidade('4', PHONE_OK, { lista: ONU_LISTA, detalhe: ONU_DETALHE,
  info: { result: 'uptime 12345 dias  temperatura 47 C  serial 9988' } }).t;
check(!/Sinal óptico/.test(tabs.reply), 'numeros sem dBm nao viram leitura de sinal');

// Diagnostico passa pela mesma validacao dos outros modulos
const diagOk = { lista: ONU_LISTA, detalhe: ONU_DETALHE, info: OLTS.huawei };
let sd = null;
let tq = turn(sd, '4', PHONE_OUTRO); sd = tq.sessionRow;
tq = turn(sd, CPF, PHONE_OUTRO, RESP, FATURAS, diagOk); sd = tq.sessionRow;
if (tq.step === 'awaiting_contract_choice') tq = turn(sd, '1', PHONE_OUTRO, RESP, FATURAS, diagOk);
check(tq.step === 'awaiting_second_factor', 'diagnostico exige 2FA como os demais');

// --- Caminho preferencial: info_rx vem pronto no /fttx/onu/list/ -----------
// Estrutura real capturada do SGP do provedor. Aqui nao ha texto de OLT para
// parsear: o proprio SGP ja guarda a ultima leitura em info_rx. E o caminho que
// vai rodar em producao, entao precisa valer mais que o fallback de regex.
function onuReal(rx, quando) {
  return [{ id: 451, olt_id: 2, olt_name: 'OLT ZTE', slot: 2, pon: 2, onuid: 1,
    type: 'F670L', vlan: 28, mode: 'PPPoE', phy_addr: 'ZTEGDA11A47B',
    info_rx: rx, info_tx: '2.326', info_olt_rx: '-20.017', info_date: quando,
    service_contrato: 999, service_status: 1 }];
}
const AGORA = new Date(Date.now() - 3600000).toISOString().slice(0, 19).replace('T', ' ');
const SEM_DETALHE = { onu: {} };

const treal = ateIdentidade('4', PHONE_OK,
  { lista: onuReal('-15.656', AGORA), detalhe: SEM_DETALHE, info: null }).t;
console.log('  bot:', JSON.stringify(treal.reply).slice(0, 170));
check(/-15\.66 dBm/.test(treal.reply), 'info_rx da lista vira o sinal exibido');
check(/Bom/.test(treal.reply), '-15.66 dBm classificado como Bom');
check(/F670L/.test(treal.reply), 'modelo da ONU vem da lista quando nao ha detalhe');
check(treal.audit.resposta_sgp.sinal_origem === 'lista', 'auditoria registra a origem do sinal');

// info_rx tem prioridade sobre o texto da OLT: se os dois existirem e
// divergirem, o valor guardado pelo SGP e o confiavel.
const tprio = ateIdentidade('4', PHONE_OK,
  { lista: onuReal('-15.656', AGORA), detalhe: ONU_DETALHE, info: OLTS.huawei }).t;
check(/-15\.66 dBm/.test(tprio.reply) && !/-19\.45/.test(tprio.reply),
      'info_rx tem prioridade sobre o parser de texto da OLT');

// Leitura antiga nao pode ser apresentada como se fosse de agora
const tvelho = ateIdentidade('4', PHONE_OK,
  { lista: onuReal('-15.656', '2026-01-02 03:04:05'), detalhe: SEM_DETALHE, info: null }).t;
check(/última leitura registrada/.test(tvelho.reply), 'leitura antiga vem com ressalva');
check(/02\/01 às 03:04/.test(tvelho.reply), 'mostra quando a leitura foi feita');

// info_rx fora da faixa fisica (campo vazio, zero, lixo) nao pode virar sinal
[['', 'vazio'], ['0', 'zero'], ['99', 'positivo absurdo'], ['-99', 'negativo absurdo']].forEach(
  function (par) {
    const t = ateIdentidade('4', PHONE_OK,
      { lista: onuReal(par[0], AGORA), detalhe: SEM_DETALHE, info: null }).t;
    check(!/Sinal óptico/.test(t.reply), 'info_rx ' + par[1] + ' nao vira leitura de sinal');
  });

// Tx e positivo: nunca pode ser confundido com o sinal recebido
const ttx = ateIdentidade('4', PHONE_OK,
  { lista: onuReal('2.326', AGORA), detalhe: SEM_DETALHE, info: null }).t;
check(!/Sinal óptico/.test(ttx.reply), 'valor de Tx nao e exibido como sinal recebido');

// ============ Wi-Fi desligado (provedor sem Gerenciador de CPE) ============
// Sem ACS cadastrado no SGP a opcao 1 falha SEMPRE, no ultimo passo, depois de
// o cliente ja ter provado quem e e escolhido nome e senha. Melhor nao oferecer.
console.log('\n=== Wi-Fi desligado por configuracao ===');
ENV = { WIFI_MODO: 'off' };

let td = turn(null, 'oi', PHONE_OK);
check(!/Alterar nome\/senha do Wi-Fi/.test(td.reply), 'opcao de Wi-Fi sai do menu');
check(/2ª via de boleto/.test(td.reply) && /Diagnóstico/.test(td.reply),
      'as outras opcoes continuam no menu');
check(/\*2\*/.test(td.reply) && /\*5\*/.test(td.reply),
      'numeros das outras opcoes nao mudam');

// Quem responde olhando uma mensagem antiga ainda digita 1. Nao pode receber
// o menu de novo sem explicacao, nem entrar num fluxo que vai falhar.
td = turn(null, '1', PHONE_OK);
check(td.step === 'menu' && !/CPF/i.test(td.reply), 'digitar 1 nao inicia o fluxo de Wi-Fi');
check(/atendente/i.test(td.reply), 'digitar 1 explica e aponta para o atendente');

// As outras opcoes seguem funcionando normalmente
td = turn(null, '2', PHONE_OK);
check(td.step === 'awaiting_cpf', 'boleto continua funcionando com o Wi-Fi desligado');

// ============ Wi-Fi por chamado (provedor sem ACS, mas quer atender) ============
// Sem ACS a alternativa real nao era "esperar": era o cliente ligar para o
// suporte. Aqui ele se identifica, escolhe o que quer, e a equipe recebe um
// pedido estruturado em vez de uma ligacao.
console.log('\n=== Wi-Fi por chamado ===');
ENV = { WIFI_MODO: 'chamado' };

let tc = ateIdentidade('1', PHONE_OK);
check(tc.t.step === 'awaiting_wifi_what', 'modo chamado mantem a opcao no menu');
let sc = turn(tc.s, '3', PHONE_OK).sessionRow;
sc = turn(sc, 'RedeDoJoao', PHONE_OK).sessionRow;
let rc = turn(sc, 'SenhaBoa123', PHONE_OK); sc = rc.sessionRow;
check(rc.step === 'awaiting_wifi_confirm', 'modo chamado tambem pede confirmacao');
check(!/desconectar/.test(rc.reply),
      'nao promete queda dos aparelhos - nada e aplicado agora');
check(/chamado/i.test(rc.reply), 'explica que vai abrir um chamado');
check(/vis[íi]vel para a equipe/i.test(rc.reply),
      'avisa que a senha ficara visivel para a equipe tecnica');

let pedido = null;
rc = turn(sc, '1', PHONE_OK, { protocolo: '202608071234' }, null, null,
          (p) => { pedido = p; });
check(/RedeDoJoao/.test(pedido.conteudo) && /SenhaBoa123/.test(pedido.conteudo),
      'o chamado leva nome e senha para o tecnico aplicar');
check(pedido.contrato === ativos[0].contratoId, 'o chamado vai no contrato certo');
check(/202608071234/.test(rc.reply), 'cliente recebe o protocolo');
check(rc.step === 'menu', 'volta ao menu depois de registrar');

// So a senha: o chamado nao pode pedir troca de nome que ninguem solicitou
tc = ateIdentidade('1', PHONE_OK);
sc = turn(tc.s, '2', PHONE_OK).sessionRow;
sc = turn(sc, 'OutraSenha77', PHONE_OK).sessionRow;
pedido = null;
turn(sc, '1', PHONE_OK, { protocolo: '1' }, null, null, (p) => { pedido = p; });
check(/OutraSenha77/.test(pedido.conteudo) && !/Novo nome/.test(pedido.conteudo),
      'so senha -> chamado nao menciona nome novo');

// ============ Wi-Fi direto na NBI do GenieACS (WIFI_MODO=genieacs) ============
// Aqui o bot deixa de pedir ao SGP e escreve ele mesmo no equipamento. O que
// o cpemanage resolvia sozinho - achar o device e saber que parametro o modelo
// aceita - passa a ser responsabilidade nossa, e e isso que estes testes
// cobrem. A regra de ouro: na duvida, NAO escreve.
console.log('\n=== Wi-Fi pela NBI do GenieACS ===');
ENV = { WIFI_MODO: 'genieacs' };

// A NBI devolve cada parametro como {_value, _writable, _timestamp}.
function par(v, writable) {
  return { _value: v, _writable: writable !== false, _timestamp: '2026-08-29T00:00:00Z' };
}
// Uma instancia de WLANConfiguration. banda = null simula firmware que nao
// informa 2.4/5 GHz - caso comum e que muda a estrategia de escolha.
function rede(banda, o) {
  o = o || {};
  const i = { Enable: par(o.enable === undefined ? true : o.enable) };
  if (o.semSsid !== true) i.SSID = par('RedeAtual', o.ssidWritable);
  if (banda) i.OperatingFrequencyBand = par(banda);
  if (o.semSenha !== true) i.KeyPassphrase = par('', o.senhaWritable);
  if (o.psk) i.PreSharedKey = { '1': { PreSharedKey: par('') } };
  return i;
}
function device(redes, id) {
  return { _id: id || 'ZTEG-F670L-ABC123',
           _deviceId: { _SerialNumber: 'ABC123', _ProductClass: 'F670L' },
           InternetGatewayDevice: { LANDevice: { '1': { WLANConfiguration: redes } } } };
}
const ACHOU = { statusCode: 200, body: [device({ '1': rede('2.4GHz'), '5': rede('5GHz') })] };
const APLICOU = { statusCode: 200, body: {} };

// Fluxo completo ate a confirmacao, no modo genieacs.
function ateConfirmar(alvo, valores) {
  const a = ateIdentidade('1', PHONE_OK);
  let ss = turn(a.s, alvo, PHONE_OK).sessionRow;
  valores.forEach(function (v) { ss = turn(ss, v, PHONE_OK).sessionRow; });
  return ss;
}

let sa = ateConfirmar('3', ['RedeNova', 'SenhaNova123']);
let ra = turn(sa, '1', PHONE_OK, null, null, null, null, { busca: ACHOU, aplicar: APLICOU });
check(ra.step === 'menu', 'aplicou pela NBI -> volta ao menu');
check(/Pronto!/.test(ra.reply), 'aplicou pela NBI -> confirma para o cliente');
check(/já está valendo/.test(ra.reply),
      'pela NBI nao promete "alguns minutos": o 200 e o roteador confirmando');

// A tarefa precisa alcancar as DUAS radios, senao o assinante fica com 2.4 GHz
// numa senha e 5 GHz noutra - falha silenciosa que vira chamado depois.
const alvos = ra.montado.acs_task.parameterValues.map(function (x) { return x[0]; });
check(alvos.some(function (c) { return /WLANConfiguration\.1\.SSID$/.test(c); }) &&
      alvos.some(function (c) { return /WLANConfiguration\.5\.SSID$/.test(c); }),
      'escreve o SSID nas duas bandas');
check(alvos.filter(function (c) { return /KeyPassphrase$/.test(c); }).length === 2,
      'escreve a senha nas duas bandas');
check(ra.montado.acs_device_id === 'ZTEG-F670L-ABC123', 'usa o _id do device encontrado');

// O log de auditoria e consultado pela equipe toda; a senha do assinante nao
// pode estar nele. A NBI ECOA a tarefa quando falha, entao isso nao e obvio.
check(!/SenhaNova123/.test(JSON.stringify(ra.audit)),
      'senha nao vai para o log de auditoria');
check(ra.audit.resposta_sgp.via === 'genieacs' && ra.audit.sucesso === true,
      'auditoria registra a via e o resultado');

// A busca precisa sair pelo usuario PPPoE do contrato - e a unica chave que o
// SGP e o equipamento enxergam com o mesmo valor.
let consulta = null;
sa = ateConfirmar('1', ['OutroNome']);
turn(sa, '1', PHONE_OK, null, null, null, function (p) { consulta = p; },
     { busca: ACHOU, aplicar: APLICOU });
check(/exemplo999/.test(consulta.acs_query || ''),
      'a busca no ACS vai pelo login PPPoE do contrato');
check(/\$or/.test(consulta.acs_query || ''),
      'sem saber o caminho do PPPoE do parque, tenta mais de um');

// So o nome: nenhum parametro de senha pode viajar
let rn = turn(sa, '1', PHONE_OK, null, null, null, null, { busca: ACHOU, aplicar: APLICOU });
const soNome = rn.montado.acs_task.parameterValues.map(function (x) { return x[0]; });
check(soNome.length === 2 && soNome.every(function (c) { return /SSID$/.test(c); }),
      'so nome -> a tarefa nao toca em senha');

// --------- Os casos em que NAO se escreve ---------
// Equipamento que nunca chegou no ACS: e o desfecho esperado para boa parte da
// base durante o rollout, nao uma excecao rara.
sa = ateConfirmar('3', ['RedeNova', 'SenhaNova123']);
let rf = turn(sa, '1', PHONE_OK, null, null, null, null,
              { busca: { statusCode: 200, body: [] } });
check(rf.step === 'human_handoff', 'device nao provisionado -> atendente');
check(/continua como estava/.test(rf.reply), 'diz que a rede nao foi mexida');
check(rf.audit.resposta_sgp.falha === 'device_nao_encontrado',
      'motivo fica no log para o time de provisionamento');
check(!/SenhaNova123/.test(JSON.stringify(rf.audit)), 'falha tambem nao loga a senha');

// Duas chaves apontando para equipamentos diferentes. Escolher um seria
// escrever no roteador de outra pessoa.
sa = ateConfirmar('3', ['RedeNova', 'SenhaNova123']);
rf = turn(sa, '1', PHONE_OK, null, null, null, null,
          { busca: { statusCode: 200, body: [device({ '1': rede('2.4GHz') }, 'A-1'),
                                             device({ '1': rede('2.4GHz') }, 'B-2')] } });
check(rf.step === 'human_handoff' && rf.audit.resposta_sgp.falha === 'device_ambiguo',
      'dois devices -> nao escolhe nenhum');

// Modelo sem a arvore de Wi-Fi lida (ou TR-181, que tem outro mapeamento)
sa = ateConfirmar('3', ['RedeNova', 'SenhaNova123']);
rf = turn(sa, '1', PHONE_OK, null, null, null, null,
          { busca: { statusCode: 200, body: [{ _id: 'X-1', _deviceId: {},
                     InternetGatewayDevice: { DeviceInfo: {} } }] } });
check(rf.audit.resposta_sgp.falha === 'sem_wlanconfiguration',
      'sem WLANConfiguration -> nao tenta adivinhar caminho');

// Senha que o modelo nao aceita escrever: aplicar so o nome deixaria o cliente
// achando que a senha mudou.
sa = ateConfirmar('3', ['RedeNova', 'SenhaNova123']);
rf = turn(sa, '1', PHONE_OK, null, null, null, null,
          { busca: { statusCode: 200,
                     body: [device({ '1': rede('2.4GHz'),
                                     '5': rede('5GHz', { senhaWritable: false }) })] } });
check(rf.audit.resposta_sgp.falha === 'senha_nao_escrivel',
      'radio que nao aceita a senha -> recusa tudo em vez de aplicar pela metade');

// --------- Escolha de quais redes mudar ---------
// Rede desligada e quase sempre a de visitantes: renomea-la nao ajuda ninguem.
sa = ateConfirmar('1', ['NomeNovo']);
let rd = turn(sa, '1', PHONE_OK, null, null, null, null,
              { busca: { statusCode: 200,
                         body: [device({ '1': rede('2.4GHz'),
                                         '2': rede('2.4GHz', { enable: false }),
                                         '5': rede('5GHz') })] },
                aplicar: APLICOU });
check(JSON.stringify(rd.montado.acs_redes) === '[1,5]', 'rede desligada fica de fora');

// Firmware que informa a banda: muda a PRIMEIRA de cada uma (a principal).
sa = ateConfirmar('1', ['NomeNovo']);
rd = turn(sa, '1', PHONE_OK, null, null, null, null,
          { busca: { statusCode: 200,
                     body: [device({ '1': rede('2.4GHz'), '2': rede('2.4GHz'),
                                     '5': rede('5GHz'), '6': rede('5GHz') })] },
            aplicar: APLICOU });
check(JSON.stringify(rd.montado.acs_redes) === '[1,5]',
      'com banda conhecida, muda so a rede principal de cada banda');

// Firmware que nao informa a banda: nao da para distinguir principal de
// visitante, entao vale o mesmo efeito de novo_ssid + novo_ssid_5g.
sa = ateConfirmar('1', ['NomeNovo']);
rd = turn(sa, '1', PHONE_OK, null, null, null, null,
          { busca: { statusCode: 200,
                     body: [device({ '1': rede(null), '5': rede(null) })] },
            aplicar: APLICOU });
check(JSON.stringify(rd.montado.acs_redes) === '[1,5]',
      'sem banda informada, muda todas as redes ligadas');

// PreSharedKey e KeyPassphrase convivem e ha modelo que so honra um deles.
sa = ateConfirmar('2', ['SenhaDupla123']);
rd = turn(sa, '1', PHONE_OK, null, null, null, null,
          { busca: { statusCode: 200,
                     body: [device({ '1': rede('2.4GHz', { psk: true }) })] },
            aplicar: APLICOU });
const dupla = rd.montado.acs_task.parameterValues.map(function (x) { return x[0]; });
check(dupla.some(function (c) { return /KeyPassphrase$/.test(c); }) &&
      dupla.some(function (c) { return /PreSharedKey\.1\.PreSharedKey$/.test(c); }),
      'quando os dois parametros de senha existem, os dois vao');

// --------- Roteador desligado: 202, tarefa na fila ---------
// Nao e erro e nao e sucesso. O cliente precisa saber que a queda vai chegar
// depois, senao ele perde a casa toda sem entender por que.
sa = ateConfirmar('3', ['RedeNova', 'SenhaNova123']);
let rq = turn(sa, '1', PHONE_OK, null, null, null, null,
              { busca: ACHOU, aplicar: { statusCode: 202, body: {} } });
check(!/Pronto!/.test(rq.reply), '202 nao e anunciado como sucesso');
check(/agendada/.test(rq.reply), '202 -> explica que a alteracao ficou agendada');
check(/senha nova/.test(rq.reply), '202 com senha -> avisa que os aparelhos vao cair depois');
check(rq.audit.enfileirado === true, 'auditoria distingue enfileirado de aplicado');

// Falha real do equipamento (parametro recusado): a NBI ecoa a tarefa - com a
// senha dentro. Ela nao pode vazar para o log.
sa = ateConfirmar('3', ['RedeNova', 'SenhaNova123']);
let rx = turn(sa, '1', PHONE_OK, null, null, null, null,
              { busca: ACHOU,
                aplicar: { statusCode: 202,
                           body: { fault: { detail: { faultCode: '9005' } },
                                   parameterValues: [['x', 'SenhaNova123']] } } });
check(rx.step === 'human_handoff', 'fault do equipamento -> atendente, nao "tente de novo"');
check(!/SenhaNova123/.test(JSON.stringify(rx.audit)),
      'eco da tarefa na falha nao leva a senha para o log');
check(rx.audit.resposta_sgp.fault.faultCode === '9005', 'o faultCode fica no log');

// ================= Wi-Fi pela OLT (WIFI_MODO=olt) =================
// O caminho que funcionou em campo: a OLT escreve na ONU por OMCI, sem ACS.
// O que muda aqui em relacao aos outros modos e que o nome e a senha terminam
// numa linha de comando de switch - entao a validacao e mais estrita, e achar
// a ONU errada significa escrever no equipamento de outra pessoa.
console.log('\n=== Wi-Fi pela OLT ===');
ENV = { WIFI_MODO: 'olt' };

// Estrutura real do /api/fttx/onu/list/ (capturada do SGP do provedor).
function onuOlt(slot, pon, onuid, phy) {
  return { id: 451, olt_id: 2, olt_name: 'OLT ZTE', slot: slot, pon: pon,
           onuid: onuid, type: 'F670L', mode: 'PPPoE',
           phy_addr: phy || 'ZTEGDA11A47B', service_contrato: 999 };
}
const UMA_ONU = [onuOlt(2, 2, 1)];
const APLICOU_OLT = { statusCode: 200, body: { ok: true } };

function ateConfirmarOlt(alvo, valores) {
  const a = ateIdentidade('1', PHONE_OK);
  let ss = turn(a.s, alvo, PHONE_OK).sessionRow;
  valores.forEach(function (v) { ss = turn(ss, v, PHONE_OK).sessionRow; });
  return ss;
}

let so = ateConfirmarOlt('3', ['RedeNova', 'SenhaNova123']);
let ro = turn(so, '1', PHONE_OK, null, null, null, null, null,
              { lista: UMA_ONU, aplicar: APLICOU_OLT });
check(ro.montado.olt_onu === 'gpon_onu-1/2/2:1',
      'slot/pon/onu do SGP viram o endereco da OLT');
check(/Pronto!/.test(ro.reply), 'aplicou pela OLT -> confirma para o cliente');
check(/já está valendo/.test(ro.reply),
      'pela OLT nao promete espera: a OLT confirma na hora');
check(ro.step === 'menu', 'volta ao menu depois de aplicar');
check(!/SenhaNova123/.test(JSON.stringify(ro.audit)),
      'senha nao vai para o log de auditoria');
check(ro.audit.resposta_sgp.via === 'olt' && ro.audit.resposta_sgp.onu === 'gpon_onu-1/2/2:1',
      'auditoria registra a via e a ONU');

// Outra posicao na OLT tem de virar outro endereco - se isto quebrar, o bot
// escreve na ONU errada e ninguem percebe ate o cliente reclamar.
so = ateConfirmarOlt('1', ['OutroNome']);
ro = turn(so, '1', PHONE_OK, null, null, null, null, null,
          { lista: [onuOlt(4, 8, 27)], aplicar: APLICOU_OLT });
check(ro.montado.olt_onu === 'gpon_onu-1/4/8:27', 'monta o endereco de outra posicao');

// --------- Os casos em que NAO se escreve ---------
so = ateConfirmarOlt('3', ['RedeNova', 'SenhaNova123']);
let rfo = turn(so, '1', PHONE_OK, null, null, null, null, null, { lista: [] });
check(rfo.step === 'human_handoff', 'contrato sem ONU no SGP -> atendente');
check(/continua como estava/.test(rfo.reply), 'diz que a rede nao foi mexida');
check(rfo.audit.resposta_sgp.falha === 'onu_nao_encontrada', 'motivo fica no log');

// Duas ONUs e nenhuma casa com o MAC do contrato. O modulo de diagnostico pega
// a primeira nesse caso - pode, porque so le. Aqui escreveria na casa errada.
so = ateConfirmarOlt('3', ['RedeNova', 'SenhaNova123']);
rfo = turn(so, '1', PHONE_OK, null, null, null, null, null,
          { lista: [onuOlt(2, 2, 1, 'ZTEGAAAA1111'), onuOlt(2, 2, 2, 'ZTEGBBBB2222')] });
check(rfo.step === 'human_handoff' && rfo.audit.resposta_sgp.falha === 'onu_ambigua',
      'duas ONUs sem desempate -> nao escolhe nenhuma');

// Com o MAC do contrato batendo, o desempate e legitimo
const macDoContrato = String(ativos[0].servico_mac || '').replace(/[^a-zA-Z0-9]/g, '');
if (macDoContrato) {
  so = ateConfirmarOlt('1', ['NomeNovo']);
  ro = turn(so, '1', PHONE_OK, null, null, null, null, null,
            { lista: [onuOlt(2, 2, 1, 'ZTEGAAAA1111'), onuOlt(3, 5, 9, macDoContrato)],
              aplicar: APLICOU_OLT });
  check(ro.montado && ro.montado.olt_onu === 'gpon_onu-1/3/5:9',
        'MAC do contrato desempata entre duas ONUs');
}

// Cadastro incompleto no SGP nao pode virar endereco pela metade
so = ateConfirmarOlt('3', ['RedeNova', 'SenhaNova123']);
rfo = turn(so, '1', PHONE_OK, null, null, null, null, null,
          { lista: [{ id: 1, olt_name: 'OLT ZTE', slot: 2, pon: null, onuid: 1 }] });
check(rfo.audit.resposta_sgp.falha === 'porta_incompleta',
      'slot/pon/onu faltando -> nao monta endereco torto');

// A OLT recusou o comando
so = ateConfirmarOlt('3', ['RedeNova', 'SenhaNova123']);
rfo = turn(so, '1', PHONE_OK, null, null, null, null, null,
          { lista: UMA_ONU,
            aplicar: { statusCode: 502, body: { ok: false, detalhe: '%Error 223845: UNI does not exist.' } } });
check(rfo.step === 'human_handoff', 'OLT recusou -> atendente, nao "tente de novo"');
check(/UNI does not exist/.test(JSON.stringify(rfo.audit)), 'o erro da OLT fica no log');

// --------- Validacao mais estrita neste modo ---------
// O que o cliente digita vai para uma linha de comando de switch. Recusar na
// digitacao e melhor que aceitar e falhar depois da confirmacao.
console.log('\n--- validacao no modo olt ---');
let vo = ateIdentidade('1', PHONE_OK);
// '3' = nome e senha, para o fluxo passar pelas duas validacoes
let svo = turn(vo.s, '3', PHONE_OK).sessionRow;

let rvo = turn(svo, 'Rede Com Espaco', PHONE_OK);
check(rvo.step === 'awaiting_ssid' && /espaços/.test(rvo.reply),
      'nome com espaco e recusado, com explicacao');
rvo = turn(svo, 'RedeComAcento' + 'ç', PHONE_OK);
check(rvo.step === 'awaiting_ssid', 'nome com acento e recusado');
rvo = turn(svo, 'Rede?', PHONE_OK);
check(rvo.step === 'awaiting_ssid', 'nome com ? e recusado (abre ajuda no CLI da OLT)');
rvo = turn(svo, 'Casa-do-Joao', PHONE_OK);
check(rvo.step === 'awaiting_password', 'nome com hifen e aceito');

svo = turn(svo, 'RedeBoa', PHONE_OK).sessionRow;
rvo = turn(svo, 'Senha Com Espaco', PHONE_OK);
check(rvo.step === 'awaiting_password' && /espaços/.test(rvo.reply), 'senha com espaco e recusada');
rvo = turn(svo, 'Senha?123', PHONE_OK);
check(rvo.step === 'awaiting_password', 'senha com ? e recusada');
rvo = turn(svo, 'SenhaBoa123', PHONE_OK);
check(rvo.step === 'awaiting_wifi_confirm', 'senha comum e aceita');

// E nos outros modos a regra estrita NAO vale - quem usa o cpemanage ou o ACS
// pode ter espaco no nome, e tirar isso seria piorar o que ja funciona.
ENV = { WIFI_MODO: 'chamado' };
v = ateIdentidade('1', PHONE_OK);
svo = turn(vo.s, '1', PHONE_OK).sessionRow;
rvo = turn(svo, 'Rede Com Espaco', PHONE_OK);
check(rvo.step === 'awaiting_wifi_confirm', 'no modo chamado, nome com espaco continua valendo');
ENV = { WIFI_MODO: 'olt' };

ENV = {};
td = turn(null, 'oi', PHONE_OK);
check(/Alterar nome\/senha do Wi-Fi/.test(td.reply), 'sem a variavel, o padrao e aplicar pelo ACS');

console.log('\n----------------------------------------');
console.log(ok + ' passaram, ' + fail + ' falharam');
process.exit(fail ? 1 : 0);
