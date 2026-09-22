'use strict';
// Disparo proativo de aviso de fatura.
//
// Uma vez por dia (FATURAS_HORA), varre TODOS os titulos do SGP
// (POST /api/ura/titulos/, paginado), separa os que estao EM ABERTO e vencem
// nos proximos FATURAS_DIAS_ANTES dias e que ainda nao foram avisados, e manda
// para cada cliente o template oficial da Meta (FATURAS_TEMPLATE) com nome,
// vencimento e valor. Prioriza quem vence primeiro e respeita o teto diario
// (FATURAS_TETO_DIA) - o limite de mensagens iniciadas pela empresa que a Meta
// impoe (250/dia sem verificacao da empresa; sobe depois).
//
// Ao enviar, grava em wa_fatura_avisada (nunca manda 2x) e PRE-SEMEIA a sessao
// do bot (wa_sessions) com a identidade daquele telefone. Assim, quando o
// cliente toca no botao "Pagar minha fatura" do template, o fluxo do n8n ja o
// reconhece e dispara PIX + boleto na hora, sem pedir CPF.
//
// SO funciona com a Evolution no Cloud API oficial (WHATSAPP-BUSINESS) e o
// template APROVADO. Enquanto isso, deixe FATURAS_ON=false: o daemon roda sem
// enviar nada.
//
// Uso:
//   node disparar-faturas.js --daemon          (agenda no horario; e o default)
//   node disparar-faturas.js --agora           (dispara uma vez agora e sai)
//   node disparar-faturas.js --agora --dry-run (mostra o que faria, sem enviar)

const { Pool } = require('pg');
const cron = require('node-cron');

// ---- Config ---------------------------------------------------------------
const SGP_URL   = (process.env.SGP_API_URL || '').replace(/\/+$/, '');
const SGP_TOKEN = process.env.SGP_API_TOKEN || '';
const SGP_APP   = process.env.SGP_APP_NAME || '';

const EVO_URL   = (process.env.EVOLUTION_API_URL || 'http://evolution:8080').replace(/\/+$/, '');
// Instancia do DISPARO (numero de faturas). Por padrao usa a mesma do bot; ao
// separar em 2 numeros, aponte FATURAS_INSTANCE para a instancia do 9234.
const EVO_INST  = process.env.FATURAS_INSTANCE || process.env.EVOLUTION_INSTANCE || '';
const EVO_KEY   = process.env.EVOLUTION_API_KEY || '';

const ON          = String(process.env.FATURAS_ON || 'false').trim().toLowerCase() === 'true';
const DIAS_ANTES  = parseInt(process.env.FATURAS_DIAS_ANTES || '15', 10);
const TETO_DIA    = parseInt(process.env.FATURAS_TETO_DIA || '250', 10);
const HORA        = String(process.env.FATURAS_HORA || '10:00').trim();
const INTERVALO   = parseInt(process.env.FATURAS_INTERVALO_MS || '1500', 10);
const TEMPLATE    = process.env.FATURAS_TEMPLATE || 'aviso_fatura';
const TEMPLATE_LG = process.env.FATURAS_TEMPLATE_LANG || 'pt_BR';
const TZ          = process.env.TZ || 'America/Sao_Paulo';

const ARGS    = process.argv.slice(2);
const DRY_RUN = ARGS.includes('--dry-run');
const AGORA   = ARGS.includes('--agora');
// --teste <numero>: manda UMA mensagem de fatura para esse numero (o seu), com
// os dados de uma fatura real, para validar o formato antes de soltar na base.
const _iT       = ARGS.indexOf('--teste');
const TESTE_NUM = _iT >= 0 ? String(ARGS[_iT + 1] || '').replace(/\D/g, '') : '';
const DAEMON  = !AGORA && !TESTE_NUM;

const pool = new Pool({
  host: process.env.POSTGRES_HOST || 'postgres',
  port: parseInt(process.env.POSTGRES_PORT || '5432', 10),
  database: process.env.POSTGRES_DB,
  user: process.env.POSTGRES_USER,
  password: process.env.POSTGRES_PASSWORD,
});

// ---- Utilitarios ----------------------------------------------------------
function log(...a)  { console.log(new Date().toISOString(), ...a); }
function warn(...a) { console.warn(new Date().toISOString(), '[!]', ...a); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Data de hoje (YYYY-MM-DD) no fuso configurado. Como os vencimentos vem em
// ISO date-only, comparacao lexicografica ja ordena e filtra corretamente.
function hojeISO() {
  const f = new Intl.DateTimeFormat('en-CA', {
    timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit',
  });
  return f.format(new Date()); // en-CA => YYYY-MM-DD
}
function addDiasISO(iso, dias) {
  const d = new Date(iso + 'T12:00:00Z');
  d.setUTCDate(d.getUTCDate() + dias);
  return d.toISOString().slice(0, 10);
}
function isoParaBR(iso) {
  const m = String(iso || '').match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? `${m[3]}/${m[2]}/${m[1]}` : String(iso || '');
}
function brl(v) {
  const n = Number(v) || 0;
  return n.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
}
// Normaliza telefone brasileiro para o formato que o WhatsApp usa (55 + DDD +
// numero). Best-effort: se nao casar exatamente com o remoteJid do cliente, o
// pior caso e o bot pedir o CPF quando ele tocar no botao.
function normalizarTelefone(raw) {
  let d = String(raw || '').replace(/\D/g, '');
  if (!d) return '';
  if (d.startsWith('55')) d = d.slice(2);
  // tira zeros/DDD-tronco eventuais
  if (d.length > 11 && d.startsWith('0')) d = d.replace(/^0+/, '');
  if (d.length < 10 || d.length > 11) return ''; // nao parece telefone valido
  return '55' + d;
}
function primeiroNome(nome) {
  const p = String(nome || '').trim().split(/\s+/);
  return p.length ? p[0].charAt(0).toUpperCase() + p[0].slice(1).toLowerCase() : '';
}

// ---- SGP ------------------------------------------------------------------
async function sgpPost(path, body) {
  const resp = await fetch(SGP_URL + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(Object.assign({ app: SGP_APP, token: SGP_TOKEN }, body)),
  });
  const txt = await resp.text();
  let json = null;
  try { json = JSON.parse(txt); } catch (_) { /* deixa json=null */ }
  return { ok: resp.ok, status: resp.status, json, txt };
}

// Puxa TODOS os titulos, pagina a pagina. Para quando a pagina volta com menos
// que o limite, quando o offset alcanca o total, ou se detecta que o SGP esta
// ignorando o offset (mesma primeira id duas vezes) - nesse caso avisa e para,
// para nao entrar em loop.
async function puxarTitulos() {
  const LIM = 250;
  let offset = 0;
  const todos = [];
  let ultimaPrimeiraId = null;
  for (let pagina = 0; pagina < 200; pagina++) {
    const r = await sgpPost('/api/ura/titulos/', { limit: LIM, offset });
    if (!r.ok || !r.json || !Array.isArray(r.json.titulos)) {
      warn('titulos: resposta inesperada na pagina', pagina, 'status', r.status);
      break;
    }
    const arr = r.json.titulos;
    if (arr.length === 0) break;
    const primeiraId = arr[0] && arr[0].id;
    if (pagina > 0 && primeiraId === ultimaPrimeiraId) {
      warn('titulos: o SGP parece ignorar o offset (paginacao repetiu). Parando em',
        todos.length, 'titulos - verifique o parametro de paginacao.');
      break;
    }
    ultimaPrimeiraId = primeiraId;
    todos.push(...arr);
    const total = r.json.paginacao && r.json.paginacao.total;
    if (arr.length < LIM) break;
    if (total && todos.length >= total) break;
    offset += LIM;
  }
  return todos;
}

async function telefoneDoContrato(contrato) {
  const r = await sgpPost('/api/ura/consultacliente/', { contrato: String(contrato) });
  if (!r.ok || !r.json || !Array.isArray(r.json.contratos)) return '';
  const cs = r.json.contratos;
  // Prefere o contrato exato; senao, o primeiro.
  const c = cs.find((x) => String(x.contratoId) === String(contrato)) || cs[0];
  if (!c || !Array.isArray(c.telefones)) return '';
  const tels = c.telefones;
  // Prefere um celular.
  const cel = tels.find((t) => /celular/i.test(t.tipoContato || '')) || tels[0];
  return cel ? normalizarTelefone(cel.contato) : '';
}

// ---- Evolution (template) -------------------------------------------------
// Formato B: cabecalho com o BOLETO em PDF (cliente baixa) + corpo com
// nome/vencimento/valor e o CODIGO PIX (copia e cola) numa variavel. Assim a
// mensagem automatica ja traz tudo, sem depender de o cliente tocar em nada.
async function enviarTemplate(numero, nome, vencBR, valorBRL, pixCode, boletoUrl) {
  const components = [];
  if (boletoUrl) {
    components.push({
      type: 'header',
      parameters: [{ type: 'document', document: { link: boletoUrl, filename: 'Fatura.pdf' } }],
    });
  }
  components.push({
    type: 'body',
    parameters: [
      { type: 'text', text: nome },
      { type: 'text', text: vencBR },
      { type: 'text', text: valorBRL },
      { type: 'text', text: String(pixCode || '').replace(/\s+/g, '') },
    ],
  });
  const body = {
    number: numero,
    name: TEMPLATE,
    language: TEMPLATE_LG,
    components,
  };
  const resp = await fetch(`${EVO_URL}/message/sendTemplate/${EVO_INST}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', apikey: EVO_KEY },
    body: JSON.stringify(body),
  });
  const txt = await resp.text();
  return { ok: resp.ok, status: resp.status, txt };
}

// ---- Persistencia ---------------------------------------------------------
async function garantirTabela() {
  await pool.query(`
    CREATE TABLE IF NOT EXISTS wa_fatura_avisada (
      contrato          TEXT NOT NULL,
      numero_documento  TEXT NOT NULL,
      vencimento        DATE,
      valor             NUMERIC(12,2),
      phone             TEXT,
      enviado_em        TIMESTAMPTZ NOT NULL DEFAULT now(),
      PRIMARY KEY (contrato, numero_documento)
    )`);
  await pool.query(
    `CREATE INDEX IF NOT EXISTS idx_wa_fatura_avisada_venc ON wa_fatura_avisada (vencimento)`);
}

async function jaAvisados(deISO) {
  const r = await pool.query(
    `SELECT contrato, numero_documento FROM wa_fatura_avisada WHERE vencimento >= $1`, [deISO]);
  const set = new Set();
  for (const row of r.rows) set.add(row.contrato + '|' + row.numero_documento);
  return set;
}

async function marcarAvisado(t, phone) {
  await pool.query(
    `INSERT INTO wa_fatura_avisada (contrato, numero_documento, vencimento, valor, phone)
     VALUES ($1,$2,$3,$4,$5) ON CONFLICT (contrato, numero_documento) DO NOTHING`,
    [String(t.contrato), String(t.doc), t.venc, t.valor, phone]);
}

// Pre-semeia a sessao do bot para o telefone, para o toque no botao "Pagar
// minha fatura" cair direto na 2a via. So sobrescreve sessoes paradas ha mais
// de 30 min, para nao atropelar um cliente que esteja conversando agora.
async function preSemearSessao(phone, cpf, contrato) {
  const data = JSON.stringify({
    cpf: String(cpf || '').replace(/\D/g, ''),
    contrato: String(contrato),
    verified_at: Date.now(),
    suspenso: false,
    valor_aberto: 0,
    intent: 'financeiro',
    ident_reaproveitada: true,
    origem: 'aviso_fatura',
  });
  await pool.query(
    `INSERT INTO wa_sessions (phone, step, data, updated_at)
     VALUES ($1, 'menu', $2::jsonb, now())
     ON CONFLICT (phone) DO UPDATE
       SET step = 'menu', data = EXCLUDED.data, updated_at = now()
       WHERE wa_sessions.updated_at < now() - interval '30 minutes'`,
    [phone, data]);
}

// ---- Rodada ---------------------------------------------------------------
async function rodar() {
  // FATURAS_ON=false desliga o ENVIO real, mas o --dry-run (que nao envia nada)
  // roda mesmo assim, para dar para validar a lista/PIX/boleto antes de ligar.
  if (!ON && !DRY_RUN) {
    log('FATURAS_ON=false - disparo desligado. Nada a fazer.',
      '(ligue depois de migrar para o Cloud API e o template ser aprovado.)',
      'Para so conferir a lista sem enviar: rode com --agora --dry-run.');
    return;
  }
  if (!SGP_URL || !SGP_TOKEN || !SGP_APP) { warn('faltam credenciais do SGP no .env'); return; }
  if (!EVO_INST || !EVO_KEY) { warn('faltam credenciais da Evolution no .env'); return; }

  const hoje = hojeISO();
  const limite = addDiasISO(hoje, DIAS_ANTES); // vence em ate N dias
  log(`Rodada: hoje=${hoje}, avisando faturas em aberto que vencem ate ${limite} (${DIAS_ANTES} dias), teto=${TETO_DIA}${DRY_RUN ? ' [DRY-RUN]' : ''}`);

  const titulos = await puxarTitulos();
  log(`SGP retornou ${titulos.length} titulos.`);

  const avisados = await jaAvisados(hoje);

  // Candidatos: em aberto, vencimento entre hoje e o limite, ainda nao avisados.
  const cand = [];
  for (const t of titulos) {
    if (String(t.status || '').toLowerCase() !== 'aberto') continue;
    const venc = String(t.dataVencimento || '').slice(0, 10);
    if (!venc || venc < hoje || venc > limite) continue;
    const doc = String(t.numeroDocumento);
    const contrato = String(t.clienteContrato);
    if (avisados.has(contrato + '|' + doc)) continue;
    cand.push({
      contrato, doc, venc, valor: t.valor,
      cpf: t.clienteCpfcnpj, nome: t.clienteNome,
      pix: t.codigoPix || '', boleto: t.link || '',
    });
  }
  // Prioriza quem vence primeiro (mais urgente sob o teto diario).
  cand.sort((a, b) => (a.venc < b.venc ? -1 : a.venc > b.venc ? 1 : 0));
  const fila = cand.slice(0, TETO_DIA);
  log(`Candidatos: ${cand.length} | enviando hoje (ate o teto): ${fila.length}` +
    (cand.length > fila.length ? ` | ${cand.length - fila.length} ficam para amanha (teto atingido)` : ''));

  let enviados = 0, semTelefone = 0, falhas = 0;
  for (const t of fila) {
    const numero = await telefoneDoContrato(t.contrato);
    if (!numero) {
      semTelefone++;
      warn(`contrato ${t.contrato} (${t.nome}) sem telefone valido - pulando`);
      continue;
    }
    if (DRY_RUN) {
      log(`[dry] -> ${numero} | ${primeiroNome(t.nome)} | vence ${isoParaBR(t.venc)} | ${brl(t.valor)} | pix:${t.pix ? 'sim' : 'nao'} | boleto:${t.boleto ? 'sim' : 'nao'}`);
      enviados++;
      continue;
    }
    const r = await enviarTemplate(numero, primeiroNome(t.nome), isoParaBR(t.venc), brl(t.valor), t.pix, t.boleto);
    if (!r.ok) {
      falhas++;
      warn(`falha ao enviar contrato ${t.contrato} -> ${numero}: HTTP ${r.status} ${r.txt.slice(0, 200)}`);
      continue;
    }
    await marcarAvisado(t, numero);
    try { await preSemearSessao(numero, t.cpf, t.contrato); } catch (e) { warn('pre-semear sessao falhou:', e.message); }
    enviados++;
    await sleep(INTERVALO);
  }
  log(`Fim da rodada. Enviados: ${enviados} | sem telefone: ${semTelefone} | falhas: ${falhas}`);
}

// Manda UMA mensagem de fatura para um numero de teste, com os dados de uma
// fatura real da janela. Nao grava nada, nao respeita teto, ignora FATURAS_ON.
async function testeUm(numero) {
  const hoje = hojeISO();
  const limite = addDiasISO(hoje, DIAS_ANTES);
  const titulos = await puxarTitulos();
  let alvo = null;
  for (const t of titulos) {
    if (String(t.status || '').toLowerCase() !== 'aberto') continue;
    const v = String(t.dataVencimento || '').slice(0, 10);
    if (!v || v < hoje || v > limite) continue;
    if (!t.codigoPix || !t.link) continue;
    alvo = t; break;
  }
  if (!alvo) { warn('nenhuma fatura na janela para usar de exemplo'); return; }
  const venc = isoParaBR(String(alvo.dataVencimento).slice(0, 10));
  log(`TESTE -> ${numero} | ${alvo.clienteNome} | vence ${venc} | ${brl(alvo.valor)} | template=${TEMPLATE}`);
  const r = await enviarTemplate(numero, primeiroNome(alvo.clienteNome), venc, brl(alvo.valor), alvo.codigoPix, alvo.link);
  log('Resposta da Evolution: HTTP', r.status);
  log(r.txt.slice(0, 500));
}

// ---- Bootstrap ------------------------------------------------------------
async function main() {
  await garantirTabela();

  if (TESTE_NUM) {
    await testeUm(TESTE_NUM).catch((e) => warn('erro no teste:', e.stack || e.message));
    await pool.end();
    return;
  }

  if (AGORA) {
    await rodar().catch((e) => warn('erro na rodada:', e.stack || e.message));
    await pool.end();
    return;
  }

  // Daemon: agenda no horario de FATURAS_HORA (HH:MM), fuso TZ.
  const m = HORA.match(/^(\d{1,2}):(\d{2})$/);
  const hh = m ? parseInt(m[1], 10) : 10;
  const mm = m ? parseInt(m[2], 10) : 0;
  const expr = `${mm} ${hh} * * *`;
  log(`Daemon ativo. Disparo agendado para ${String(hh).padStart(2, '0')}:${String(mm).padStart(2, '0')} (${TZ}). FATURAS_ON=${ON}.`);
  cron.schedule(expr, () => {
    rodar().catch((e) => warn('erro na rodada:', e.stack || e.message));
  }, { timezone: TZ });
}

main().catch((e) => { warn('erro fatal:', e.stack || e.message); process.exit(1); });
