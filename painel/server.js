'use strict';
// Painel de atendimentos do bot.
//
// Le o MESMO Postgres do bot (so leitura, no que e do bot) e serve uma pagina
// unica para a equipe do provedor: historico de atendimentos, consulta de
// cliente, conversas e um resumo com numeros. Login por usuario/senha; todos
// veem tudo, so o admin gerencia contas.
//
// O que ele NUNCA faz: guardar senha em claro (bcrypt), e nunca escreve nas
// tabelas do bot - so na painel_users. As telas sao read-only sobre a operacao.

const express = require('express');
const cookieParser = require('cookie-parser');
const bcrypt = require('bcryptjs');
const jwt = require('jsonwebtoken');
const fs = require('fs');
const path = require('path');
const { Pool } = require('pg');

const PORTA = parseInt(process.env.PAINEL_PORT || '8090', 10);
const JWT_SECRET = process.env.PAINEL_JWT_SECRET || '';
const SGP_URL = (process.env.SGP_API_URL || '').replace(/\/+$/, '');
const SGP_TOKEN = process.env.SGP_API_TOKEN || '';
const SGP_APP = process.env.SGP_APP_NAME || '';
// Para o atendente responder pelo painel, o painel fala com a Evolution (mesma
// rede interna do bot). Sem essas variaveis, o envio fica indisponivel.
const EVO_URL = (process.env.EVOLUTION_API_URL || 'http://evolution:8080').replace(/\/+$/, '');
const EVO_INST = process.env.EVOLUTION_INSTANCE || '';
const EVO_KEY = process.env.EVOLUTION_API_KEY || '';

if (!JWT_SECRET || JWT_SECRET.length < 16) {
  console.error('[x] PAINEL_JWT_SECRET ausente ou curto. Gere com: openssl rand -hex 32');
  process.exit(1);
}

const pool = new Pool({
  host: process.env.POSTGRES_HOST || 'postgres',
  port: parseInt(process.env.POSTGRES_PORT || '5432', 10),
  database: process.env.POSTGRES_DB,
  user: process.env.POSTGRES_USER,
  password: process.env.POSTGRES_PASSWORD,
  max: 5,
});

// ---------------------------------------------------------------- arranque
async function migrar() {
  const sql = fs.readFileSync(path.join(__dirname, 'migrations.sql'), 'utf8');
  await pool.query(sql);

  // Primeiro admin: nasce das variaveis de ambiente, so se ainda nao houver
  // nenhuma conta. Depois disso, contas se criam pelo proprio painel.
  const { rows } = await pool.query('SELECT COUNT(*)::int AS n FROM painel_users');
  if (rows[0].n === 0) {
    const u = (process.env.PAINEL_ADMIN_USER || 'admin').trim();
    const p = process.env.PAINEL_ADMIN_PASS || '';
    if (!p) {
      console.error('[x] Sem contas e sem PAINEL_ADMIN_PASS: crie a primeira conta ' +
                    'definindo PAINEL_ADMIN_USER/PAINEL_ADMIN_PASS no .env e reiniciando.');
      return;
    }
    const hash = await bcrypt.hash(p, 10);
    await pool.query(
      "INSERT INTO painel_users (usuario, senha_hash, papel) VALUES ($1,$2,'admin')",
      [u, hash]);
    console.log('[ok] Conta admin inicial criada: ' + u);
  }
}

// ---------------------------------------------------------------- auth
function assinar(user) {
  return jwt.sign({ id: user.id, usuario: user.usuario, papel: user.papel },
                  JWT_SECRET, { expiresIn: '12h' });
}
function autenticar(req, res, next) {
  const tok = req.cookies && req.cookies.painel_sessao;
  if (!tok) return res.status(401).json({ erro: 'nao_autenticado' });
  try {
    req.user = jwt.verify(tok, JWT_SECRET);
    next();
  } catch (e) {
    res.status(401).json({ erro: 'sessao_invalida' });
  }
}
function soAdmin(req, res, next) {
  if (req.user && req.user.papel === 'admin') return next();
  res.status(403).json({ erro: 'apenas_admin' });
}

// ---------------------------------------------------------------- app
const app = express();
app.disable('x-powered-by');
app.use(express.json({ limit: '256kb' }));
app.use(cookieParser());

app.get('/api/saude', (req, res) => res.json({ ok: true }));

app.post('/api/login', async (req, res) => {
  const usuario = String((req.body && req.body.usuario) || '').trim();
  const senha = String((req.body && req.body.senha) || '');
  if (!usuario || !senha) return res.status(400).json({ erro: 'faltam_campos' });
  try {
    const { rows } = await pool.query(
      'SELECT * FROM painel_users WHERE usuario=$1 AND ativo=TRUE', [usuario]);
    const user = rows[0];
    // Compara sempre, mesmo sem usuario, para nao vazar quem existe pelo tempo.
    const ok = user ? await bcrypt.compare(senha, user.senha_hash)
                    : await bcrypt.compare(senha, '$2a$10$invalidinvalidinvalidinvalidinvalidinvalidin');
    if (!user || !ok) return res.status(401).json({ erro: 'credenciais_invalidas' });
    await pool.query('UPDATE painel_users SET ultimo_login=now() WHERE id=$1', [user.id]);
    res.cookie('painel_sessao', assinar(user), {
      httpOnly: true, sameSite: 'lax', secure: true, maxAge: 12 * 3600 * 1000 });
    res.json({ usuario: user.usuario, papel: user.papel });
  } catch (e) {
    console.error('login:', e.message);
    res.status(500).json({ erro: 'interno' });
  }
});

app.post('/api/logout', (req, res) => {
  res.clearCookie('painel_sessao');
  res.json({ ok: true });
});

app.get('/api/eu', autenticar, (req, res) => {
  res.json({ usuario: req.user.usuario, papel: req.user.papel });
});

// ---- Historico de atendimentos (le wa_wifi_change_log, do bot) ----
app.get('/api/atendimentos', autenticar, async (req, res) => {
  const tipo = String(req.query.tipo || '').trim();
  const q = String(req.query.q || '').trim();
  const limite = Math.min(parseInt(req.query.limite || '100', 10) || 100, 500);
  const where = [];
  const args = [];
  if (tipo) { args.push(tipo); where.push('tipo = $' + args.length); }
  if (q) {
    args.push('%' + q + '%');
    const i = '$' + args.length;
    where.push('(phone ILIKE ' + i + ' OR cpf ILIKE ' + i + ' OR contrato_id ILIKE ' + i + ')');
  }
  const sql = 'SELECT id, phone, cpf, contrato_id, cpe_id, ssid_novo, sucesso, ' +
              'resposta_sgp, tipo, created_at FROM wa_wifi_change_log' +
              (where.length ? ' WHERE ' + where.join(' AND ') : '') +
              ' ORDER BY id DESC LIMIT ' + limite;
  try {
    const { rows } = await pool.query(sql, args);
    res.json(rows);
  } catch (e) {
    console.error('atendimentos:', e.message);
    res.status(500).json({ erro: 'interno' });
  }
});

// ---- Resumo / numeros ----
app.get('/api/resumo', autenticar, async (req, res) => {
  try {
    const porTipo = await pool.query(
      "SELECT tipo, COUNT(*)::int n, SUM(CASE WHEN sucesso THEN 1 ELSE 0 END)::int ok " +
      "FROM wa_wifi_change_log GROUP BY tipo ORDER BY n DESC");
    const hoje = await pool.query(
      "SELECT COUNT(*)::int n FROM wa_wifi_change_log WHERE created_at >= date_trunc('day', now())");
    const semana = await pool.query(
      "SELECT to_char(date_trunc('day', created_at),'DD/MM') dia, COUNT(*)::int n " +
      "FROM wa_wifi_change_log WHERE created_at >= now() - interval '7 days' " +
      "GROUP BY 1 ORDER BY min(created_at)");
    // OS de Huawei sem TR-069: aparecem no log com esse motivo na resposta.
    const huaweiOs = await pool.query(
      "SELECT COUNT(*)::int n FROM wa_wifi_change_log " +
      "WHERE resposta_sgp::text ILIKE '%huawei_sem_tr069%' OR resposta_sgp::text ILIKE '%TR069_INTERNET%'");
    res.json({
      por_tipo: porTipo.rows, hoje: hoje.rows[0].n,
      semana: semana.rows, huawei_os: huaweiOs.rows[0].n,
    });
  } catch (e) {
    console.error('resumo:', e.message);
    res.status(500).json({ erro: 'interno' });
  }
});

// ---- Conversas ----
app.get('/api/conversas', autenticar, async (req, res) => {
  const phone = String(req.query.phone || '').trim();
  try {
    if (phone) {
      const { rows } = await pool.query(
        'SELECT direcao, texto, created_at FROM wa_messages WHERE phone=$1 ' +
        'ORDER BY id ASC LIMIT 500', [phone]);
      return res.json(rows);
    }
    // Lista de conversas: ultimo texto por telefone + se esta em atendimento
    // humano (para o painel marcar e priorizar quem espera uma pessoa).
    const { rows } = await pool.query(
      "SELECT DISTINCT ON (m.phone) m.phone, m.texto, m.direcao, m.created_at, " +
      "  COALESCE(h.ativo, false) AS humano " +
      "FROM wa_messages m LEFT JOIN wa_humano h ON h.phone = m.phone " +
      "ORDER BY m.phone, m.id DESC");
    rows.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
    res.json(rows.slice(0, 200));
  } catch (e) {
    console.error('conversas:', e.message);
    res.status(500).json({ erro: 'interno' });
  }
});

// Faturas em aberto de um contrato. nao_gerar_os=1: sem isso o SGP abriria uma
// ordem de servico a cada consulta - o painel so olha, nunca deve gerar OS.
async function faturasDoContrato(contrato) {
  try {
    const body = new URLSearchParams({ app: SGP_APP, token: SGP_TOKEN,
      contrato: String(contrato), nao_gerar_os: '1' });
    const r = await fetch(SGP_URL + '/api/ura/fatura2via/', {
      method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: body, signal: AbortSignal.timeout(15000),
    });
    const j = await r.json().catch(() => ({}));
    const links = Array.isArray(j && j.links) ? j.links : [];
    // Mesma regra do bot: vencidas + mes atual; nunca as de meses a frente.
    const limiteYM = new Date().getFullYear() * 100 + (new Date().getMonth() + 1);
    const ymVenc = (iso) => {
      const m = String(iso || '').match(/^(\d{4})-(\d{2})/);
      return m ? (Number(m[1]) * 100 + Number(m[2])) : 0;
    };
    return links.filter((f) => ymVenc(f.vencimento) <= limiteYM).map((f) => ({
      vencimento: f.vencimento, valor: f.valor, linhadigitavel: f.linhadigitavel || null,
    }));
  } catch (e) { return null; } // null = nao deu para consultar (SGP fora)
}

// ---- Atendimento humano: assumir / devolver / enviar ----
// Estado de uma conversa: em atendimento humano ou com o bot.
app.get('/api/humano', autenticar, async (req, res) => {
  const phone = String(req.query.phone || '').replace(/\D/g, '');
  if (!phone) return res.status(400).json({ erro: 'informe_phone' });
  const { rows } = await pool.query(
    'SELECT ativo, atendente FROM wa_humano WHERE phone=$1', [phone]);
  res.json(rows[0] || { ativo: false, atendente: null });
});

// Assumir (ativo=true) ou devolver ao bot (ativo=false). Ao devolver, a sessao
// do bot e apagada, para ele recomecar do menu em vez de cair no "voce esta na
// fila" que ficou gravado.
app.post('/api/humano', autenticar, async (req, res) => {
  const phone = String((req.body && req.body.phone) || '').replace(/\D/g, '');
  const ativo = !!(req.body && req.body.ativo);
  if (!phone) return res.status(400).json({ erro: 'informe_phone' });
  try {
    await pool.query(
      'INSERT INTO wa_humano (phone, ativo, atendente, atualizado_em) VALUES ($1,$2,$3,now()) ' +
      'ON CONFLICT (phone) DO UPDATE SET ativo=$2, atendente=$3, atualizado_em=now()',
      [phone, ativo, ativo ? req.user.usuario : null]);
    if (!ativo) await pool.query('DELETE FROM wa_sessions WHERE phone=$1', [phone]);
    res.json({ ok: true, ativo: ativo });
  } catch (e) {
    console.error('humano:', e.message);
    res.status(500).json({ erro: 'interno' });
  }
});

// Enviar mensagem ao cliente pelo WhatsApp (via Evolution). Assumir esta
// implicito: quem responde pelo painel esta atendendo, entao liga o modo humano
// para o bot nao responder junto. Grava a saida como 'out' (com o atendente).
app.post('/api/enviar', autenticar, async (req, res) => {
  const phone = String((req.body && req.body.phone) || '').replace(/\D/g, '');
  const texto = String((req.body && req.body.texto) || '').trim();
  if (!phone || !texto) return res.status(400).json({ erro: 'faltam_campos' });
  if (texto.length > 4096) return res.status(400).json({ erro: 'texto_longo' });
  if (!EVO_INST || !EVO_KEY) return res.status(503).json({ erro: 'evolution_nao_configurada' });
  try {
    await pool.query(
      'INSERT INTO wa_humano (phone, ativo, atendente, atualizado_em) VALUES ($1,true,$2,now()) ' +
      'ON CONFLICT (phone) DO UPDATE SET ativo=true, atendente=$2, atualizado_em=now()',
      [phone, req.user.usuario]);
    const r = await fetch(EVO_URL + '/message/sendText/' + encodeURIComponent(EVO_INST), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', apikey: EVO_KEY },
      body: JSON.stringify({ number: phone, text: texto }),
      signal: AbortSignal.timeout(20000),
    });
    if (!r.ok) {
      const t = await r.text().catch(() => '');
      console.error('evolution send:', r.status, t.slice(0, 200));
      return res.status(502).json({ erro: 'falha_envio' });
    }
    // Prefixo com o atendente, para o historico distinguir de quem foi.
    await pool.query(
      "INSERT INTO wa_messages (phone, direcao, texto) VALUES ($1,'out',$2)",
      [phone, '[' + req.user.usuario + '] ' + texto]);
    res.json({ ok: true });
  } catch (e) {
    console.error('enviar:', e.message);
    res.status(502).json({ erro: 'falha_envio' });
  }
});

// ---- Consulta de cliente (SGP + faturas + historico no bot) ----
app.get('/api/cliente', autenticar, async (req, res) => {
  const doc = String(req.query.doc || '').replace(/\D/g, '');
  if (!doc) return res.status(400).json({ erro: 'informe_cpf_cnpj' });
  if (!SGP_URL || !SGP_TOKEN) return res.status(503).json({ erro: 'sgp_nao_configurado' });
  try {
    const r = await fetch(SGP_URL + '/api/ura/consultacliente/', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ app: SGP_APP, token: SGP_TOKEN, cpfcnpj: doc }),
      signal: AbortSignal.timeout(15000),
    });
    const sgp = await r.json().catch(() => ({}));
    const contratos = Array.isArray(sgp && sgp.contratos) ? sgp.contratos : [];
    // Faturas por contrato, em paralelo. Um cliente costuma ter 1-2 contratos.
    await Promise.all(contratos.map(async (c) => {
      c._faturas = await faturasDoContrato(c.contratoId);
    }));
    const hist = await pool.query(
      'SELECT tipo, sucesso, created_at, resposta_sgp FROM wa_wifi_change_log ' +
      'WHERE cpf=$1 ORDER BY id DESC LIMIT 50', [doc]);
    res.json({ sgp: sgp, historico: hist.rows });
  } catch (e) {
    console.error('cliente:', e.message);
    res.status(502).json({ erro: 'sgp_indisponivel' });
  }
});

// ---- Gestao de usuarios (so admin) ----
app.get('/api/usuarios', autenticar, soAdmin, async (req, res) => {
  const { rows } = await pool.query(
    'SELECT id, usuario, papel, ativo, criado_em, ultimo_login FROM painel_users ORDER BY usuario');
  res.json(rows);
});
app.post('/api/usuarios', autenticar, soAdmin, async (req, res) => {
  const usuario = String((req.body && req.body.usuario) || '').trim();
  const senha = String((req.body && req.body.senha) || '');
  const papel = (req.body && req.body.papel) === 'admin' ? 'admin' : 'user';
  if (!usuario || senha.length < 6) return res.status(400).json({ erro: 'usuario_ou_senha_curta' });
  try {
    const hash = await bcrypt.hash(senha, 10);
    await pool.query('INSERT INTO painel_users (usuario, senha_hash, papel) VALUES ($1,$2,$3)',
                     [usuario, hash, papel]);
    res.json({ ok: true });
  } catch (e) {
    if (e.code === '23505') return res.status(409).json({ erro: 'usuario_ja_existe' });
    console.error('criar usuario:', e.message);
    res.status(500).json({ erro: 'interno' });
  }
});
app.post('/api/usuarios/:id/senha', autenticar, soAdmin, async (req, res) => {
  const senha = String((req.body && req.body.senha) || '');
  if (senha.length < 6) return res.status(400).json({ erro: 'senha_curta' });
  const hash = await bcrypt.hash(senha, 10);
  await pool.query('UPDATE painel_users SET senha_hash=$1 WHERE id=$2', [hash, req.params.id]);
  res.json({ ok: true });
});
app.post('/api/usuarios/:id/ativo', autenticar, soAdmin, async (req, res) => {
  const ativo = !!(req.body && req.body.ativo);
  // Nao deixa o admin desativar a propria conta e se trancar do lado de fora.
  if (String(req.user.id) === String(req.params.id) && !ativo)
    return res.status(400).json({ erro: 'nao_desative_a_si' });
  await pool.query('UPDATE painel_users SET ativo=$1 WHERE id=$2', [ativo, req.params.id]);
  res.json({ ok: true });
});

// ---- estaticos ----
app.use(express.static(path.join(__dirname, 'public')));
app.get('*', (req, res) => res.sendFile(path.join(__dirname, 'public', 'index.html')));

// So sobe sozinho quando executado direto. Quando importado (teste), quem
// controla o arranque e o teste - assim da para injetar um banco de mentira.
if (require.main === module) {
  migrar()
    .then(() => app.listen(PORTA, () => console.log('painel ouvindo na porta ' + PORTA)))
    .catch((e) => { console.error('falha no arranque:', e.message); process.exit(1); });
}

module.exports = { app, pool };
