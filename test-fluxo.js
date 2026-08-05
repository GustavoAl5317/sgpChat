// Simula o fluxo do bot rodando os Code nodes do workflow fora do n8n,
// usando a RESPOSTA REAL capturada da API demo do SGP.
// Rode: node test-fluxo.js
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
const CPF_TESTE = '52998224725'; // CPF sinteticamente valido
RESP.contratos.forEach(c => { c.cpfCnpj = '529.982.247-25'; });
const CONTRATOS = RESP.contratos;

function inbound(text, phone) {
  return run('Extract Inbound', [{
    event: 'messages.upsert',
    data: { key: { remoteJid: phone + '@s.whatsapp.net', fromMe: false }, message: { conversation: text } },
  }], {});
}

let ok = 0, fail = 0;
function check(cond, msg) {
  if (cond) { console.log('  OK   ' + msg); ok++; }
  else { console.log('  FALHA ' + msg); fail++; }
}

// Motor: aplica uma mensagem sobre uma sessão e devolve a nova sessão
function turn(sessionRow, text, phone, sgpResponse) {
  const inb = inbound(text, phone);
  if (!inb) return { skipped: true, sessionRow };
  let r = run('Parse & Route', sessionRow ? [sessionRow] : [], { 'Extract Inbound': inb });
  if (r.sgp_action === 'lookup_cpf') {
    r = run('Processar Consulta CPF', [sgpResponse], { 'Parse & Route': r });
  } else if (r.sgp_action === 'definir_wifi') {
    r = run('Processar Definir Wifi', [sgpResponse], { 'Parse & Route': r });
  }
  const p = run('Preparar Persistencia', [r], {});
  return { reply: p.reply_text, step: p.step, sessionRow: { step: p.step, data: p.data },
           data: JSON.parse(p.data), audit: p.audit, sgp_payload: r.sgp_payload };
}

const CPF = CONTRATOS[0].cpfCnpj.replace(/\D/g, '');
const ativos = CONTRATOS.filter(c => c.contratoStatus === 1);
const telReal = ativos[0].telefones[0].contato.replace(/\D/g, '');
const PHONE_OK = '55' + telReal;            // telefone cadastrado -> sem 2FA
const PHONE_OUTRO = '5599911112222';        // telefone desconhecido -> exige 2FA

console.log('Dados reais da base demo:');
console.log('  contratos retornados :', CONTRATOS.length, '| ativos:', ativos.length);
console.log('  dataNascimento (SGP) :', ativos[0].dataNascimento, '(formato ISO)');
console.log('  status distintos     :', [...new Set(CONTRATOS.map(c => c.contratoStatusDisplay))].join(', '));

console.log('\n=== 1) Telefone cadastrado (deve pular o 2FA) ===');
let s = null, t;
t = turn(s, '1', PHONE_OK); s = t.sessionRow;
check(t.step === 'awaiting_cpf', 'menu -> pede CPF');
t = turn(s, CPF, PHONE_OK, RESP); s = t.sessionRow;
console.log('  bot:', JSON.stringify(t.reply).slice(0, 110));
check(['awaiting_ssid', 'awaiting_contract_choice'].includes(t.step),
      'CPF ok + telefone bate -> sem 2FA (step=' + t.step + ')');

if (t.step === 'awaiting_contract_choice') {
  check(Array.isArray(t.data.contract_options) && t.data.contract_options.length > 1,
        'multiplos contratos ativos -> pede escolha (' + t.data.contract_options.length + ' opcoes)');
  t = turn(s, '1', PHONE_OK); s = t.sessionRow;
  check(t.step === 'awaiting_ssid', 'escolheu contrato -> pede SSID');
  check(!!t.data.contrato, 'contrato gravado na sessao: ' + t.data.contrato);
}

t = turn(s, 'Wifi da Familia', PHONE_OK); s = t.sessionRow;
check(t.step === 'awaiting_password', 'SSID aceito -> pede senha');
t = turn(s, 'abc', PHONE_OK); s = t.sessionRow;
check(t.step === 'awaiting_password', 'senha curta rejeitada');
t = turn(s, 'MinhaSenha123', PHONE_OK, { msg: 'Alterações realizadas com sucesso.', success: true });
s = t.sessionRow;
console.log('  bot:', JSON.stringify(t.reply).slice(0, 130));
check(t.step === 'menu', 'sucesso -> volta ao menu');
check(!t.data.cpf && !t.data.contrato, 'dados sensiveis limpos da sessao apos sucesso');
check(!!t.audit, 'registro de auditoria gerado');

console.log('\n=== 2) Telefone NAO cadastrado (deve exigir data de nascimento) ===');
s = null;
t = turn(s, '1', PHONE_OUTRO); s = t.sessionRow;
t = turn(s, CPF, PHONE_OUTRO, RESP); s = t.sessionRow;
console.log('  bot:', JSON.stringify(t.reply).slice(0, 110));
if (t.step === 'awaiting_contract_choice') {
  check(t.data.second_factor_pending === true, 'marca 2FA pendente antes da escolha de contrato');
  t = turn(s, '1', PHONE_OUTRO); s = t.sessionRow;
}
check(t.step === 'awaiting_second_factor', 'telefone desconhecido -> exige 2FA');

// O SGP entrega ISO (AAAA-MM-DD); o cliente digita DD/MM/AAAA
const iso = ativos[0].dataNascimento;
const br = iso.slice(8, 10) + '/' + iso.slice(5, 7) + '/' + iso.slice(0, 4);
t = turn(s, '01/01/1990', PHONE_OUTRO); s = t.sessionRow;
check(t.step === 'awaiting_second_factor', 'data errada -> continua pedindo');
t = turn(s, br, PHONE_OUTRO); s = t.sessionRow;
check(t.step === 'awaiting_ssid', 'data correta em DD/MM/AAAA casa com ISO do SGP -> libera');

console.log('\n=== 3) Bloqueios de seguranca ===');
s = null;
t = turn(s, '1', PHONE_OUTRO); s = t.sessionRow;
for (let i = 1; i <= 3; i++) { t = turn(s, '11111111111', PHONE_OUTRO); s = t.sessionRow; }
check(t.step === 'human_handoff', '3 CPFs invalidos -> atendimento humano');

s = { step: 'awaiting_second_factor', data: JSON.stringify({ attempts: 0, second_factor_target: '1990-05-20' }) };
for (let i = 1; i <= 3; i++) { t = turn(s, '01/01/1900', PHONE_OUTRO); s = t.sessionRow; }
check(t.step === 'human_handoff', '3 erros de 2FA -> atendimento humano');

console.log('\n=== 4) CPF sem contrato e roteador sem ACS ===');
t = turn({ step: 'awaiting_cpf', data: '{}' }, CPF, PHONE_OK, { msg: 'x', contratos: [] });
check(t.step === 'menu', 'CPF sem contrato -> mensagem e volta ao menu');

t = turn({ step: 'awaiting_cpf', data: '{}' }, CPF, PHONE_OK,
         { msg: 'x', contratos: CONTRATOS.filter(c => c.contratoStatus !== 1) });
check(t.step === 'human_handoff', 'so contratos inativos -> atendimento humano');

t = turn({ step: 'awaiting_password', data: JSON.stringify({ contrato: 566, ssid_new: 'Teste' }) },
         'SenhaValida123', PHONE_OK,
         { msg: 'O Serviço de internet não possui Gerenciador de CPE configurado.', success: false });
console.log('  bot:', JSON.stringify(t.reply).slice(0, 120));
check(t.step === 'human_handoff', 'roteador sem CPE (resposta real do SGP) -> atendimento humano');

console.log('\n=== 5) Comando "menu" reinicia ===');
t = turn({ step: 'awaiting_password', data: JSON.stringify({ cpf: CPF, contrato: 566 }) }, 'menu', PHONE_OK);
check(t.step === 'menu' && Object.keys(t.data).length === 0, '"menu" limpa a sessao inteira');

console.log('\n----------------------------------------');
console.log(ok + ' passaram, ' + fail + ' falharam');
process.exit(fail ? 1 : 0);
