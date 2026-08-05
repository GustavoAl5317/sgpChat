// Roda os Code nodes do workflow fora do n8n, usando RESPOSTAS REAIS capturadas
// da API demo do SGP. Cobre os tres modulos: Wi-Fi, financeiro e suporte.
//
// Uso: node test-fluxo.js <resposta-consultacliente.json>
const fs = require('fs');

const wf = JSON.parse(fs.readFileSync('n8n/workflow-wifi-selfservice.json', 'utf8'));
const code = {};
wf.nodes.forEach(n => { if (n.parameters && n.parameters.jsCode) code[n.name] = n.parameters.jsCode; });

function run(nodeName, inputs, refs) {
  const fn = new Function('$input', '$', code[nodeName]);
  const $input = { first: () => ({ json: inputs[0] }), all: () => inputs.map(j => ({ json: j })) };
  const $ = (r) => ({ first: () => ({ json: refs[r] }) });
  const out = fn($input, $);
  return out.length ? out[0].json : null;
}

const RESP = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));

// A base demo cadastra os clientes com o CPF placeholder 000.000.000-00, que
// (corretamente) nao passa na validacao de digito verificador. Para exercitar o
// fluxo mantendo a ESTRUTURA real da resposta, injetamos um CPF valido.
const CPF = '52998224725';
RESP.contratos.forEach(c => { c.cpfCnpj = '529.982.247-25'; });
const CONTRATOS = RESP.contratos;
const ativos = CONTRATOS.filter(c => c.contratoStatus === 1);

function inbound(text, phone) {
  return run('Extract Inbound', [{
    event: 'messages.upsert',
    data: { key: { remoteJid: phone + '@s.whatsapp.net', fromMe: false }, message: { conversation: text } },
  }], {});
}

// Um turno completo: mensagem do cliente -> resposta do bot + nova sessao.
// `sgpResponse` e a resposta da chamada que o turno dispara; `faturas` so e
// usada quando o turno encadeia consulta de cliente + busca de faturas.
function turn(sessionRow, text, phone, sgpResponse, faturas, diag) {
  const inb = inbound(text, phone);
  if (!inb) return { skipped: true, sessionRow };
  let r = run('Parse & Route', sessionRow ? [sessionRow] : [], { 'Extract Inbound': inb });

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
           data: JSON.parse(p.data), audit: p.audit ? JSON.parse(p.audit) : null };
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
check(t.step === 'awaiting_ssid', 'menu 1 + identidade -> pede SSID');
t = turn(s, 'Wifi da Familia', PHONE_OK); s = t.sessionRow;
check(t.step === 'awaiting_password', 'SSID aceito -> pede senha');
t = turn(s, 'abc', PHONE_OK); s = t.sessionRow;
check(t.step === 'awaiting_password', 'senha curta rejeitada');
t = turn(s, 'MinhaSenha123', PHONE_OK, { msg: 'Alteracoes realizadas com sucesso.', success: true });
check(t.step === 'menu', 'sucesso -> volta ao menu');
check(Object.keys(t.data).length === 0, 'sessao limpa apos concluir');
check(t.audit && t.audit.tipo === 'wifi', 'auditoria tipo=wifi');

// roteador sem ACS (resposta real da demo)
t = turn({ step: 'awaiting_password', data: JSON.stringify({ contrato: 566, ssid_new: 'X' }) },
         'SenhaValida123', PHONE_OK,
         { msg: 'O Servico de internet nao possui Gerenciador de CPE configurado.', success: false });
check(t.step === 'human_handoff', 'roteador sem CPE -> atendente');

// ========================= MODULO 2: Financeiro =========================
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
check(/nao tem nenhuma fatura em aberto/i.test(t.reply || ''), 'sem faturas -> mensagem apropriada');

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
const grupo = run('Extract Inbound', [{ event: 'messages.upsert',
  data: { key: { remoteJid: '123456@g.us', fromMe: false }, message: { conversation: 'oi' } } }], {});
check(grupo === null, 'mensagem de grupo e ignorada');
const propria = run('Extract Inbound', [{ event: 'messages.upsert',
  data: { key: { remoteJid: '5511999999999@s.whatsapp.net', fromMe: true }, message: { conversation: 'oi' } } }], {});
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
  const temSinal = /Sinal optico/.test(td.reply || '');
  if (vendor === 'falha') {
    check(!temSinal && /Nao consegui medir o sinal/.test(td.reply),
          'OLT fora do ar -> nao inventa sinal, avisa que nao mediu');
    check(/CTO-CENTRO-07/.test(td.reply), '  ...mas ainda entrega CTO e equipamento');
  } else if (vendor === 'ruim') {
    check(/Ruim/.test(td.reply) && /visita tecnica/.test(td.reply),
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
check(/Nao localizei o equipamento/.test(tsem.reply || ''), 'sem ONU vinculada -> mensagem clara');

// Numeros soltos no texto da OLT nao podem virar leitura de sinal
const tabs = ateIdentidade('4', PHONE_OK, { lista: ONU_LISTA, detalhe: ONU_DETALHE,
  info: { result: 'uptime 12345 dias  temperatura 47 C  serial 9988' } }).t;
check(!/Sinal optico/.test(tabs.reply), 'numeros sem dBm nao viram leitura de sinal');

// Diagnostico passa pela mesma validacao dos outros modulos
const diagOk = { lista: ONU_LISTA, detalhe: ONU_DETALHE, info: OLTS.huawei };
let sd = null;
let tq = turn(sd, '4', PHONE_OUTRO); sd = tq.sessionRow;
tq = turn(sd, CPF, PHONE_OUTRO, RESP, FATURAS, diagOk); sd = tq.sessionRow;
if (tq.step === 'awaiting_contract_choice') tq = turn(sd, '1', PHONE_OUTRO, RESP, FATURAS, diagOk);
check(tq.step === 'awaiting_second_factor', 'diagnostico exige 2FA como os demais');

console.log('\n----------------------------------------');
console.log(ok + ' passaram, ' + fail + ' falharam');
process.exit(fail ? 1 : 0);
