'use strict';
// Teste de fumaca do painel com um banco de mentira. Nao precisa de Postgres:
// substitui pool.query por um stub roteado por SQL. Cobre o que tem risco -
// login, cookie de sessao, e o portao de admin.
//
//   node test-painel.js

process.env.PAINEL_JWT_SECRET = 'x'.repeat(40);
process.env.POSTGRES_DB = 'x'; process.env.POSTGRES_USER = 'x';
process.env.POSTGRES_PASSWORD = 'x';

const http = require('http');
const bcrypt = require('bcryptjs');

const { app, pool } = require('./server.js');

// Banco de mentira: uma tabela de usuarios em memoria.
const admHash = bcrypt.hashSync('segredo123', 10);
const users = [
  { id: 1, usuario: 'chefe', senha_hash: admHash, papel: 'admin', ativo: true },
  { id: 2, usuario: 'atendente', senha_hash: bcrypt.hashSync('atende123', 10), papel: 'user', ativo: true },
];
let proxId = 3;
pool.query = async (sql, args) => {
  if (/FROM painel_users WHERE usuario/.test(sql)) {
    const u = users.find(x => x.usuario === args[0] && x.ativo);
    return { rows: u ? [u] : [] };
  }
  if (/UPDATE painel_users SET ultimo_login/.test(sql)) return { rows: [] };
  if (/INSERT INTO painel_users/.test(sql)) {
    if (users.some(u => u.usuario === args[0])) { const e = new Error('dup'); e.code = '23505'; throw e; }
    users.push({ id: proxId++, usuario: args[0], senha_hash: args[1], papel: args[2], ativo: true });
    return { rows: [] };
  }
  if (/UPDATE painel_users SET ativo/.test(sql)) {
    const u = users.find(x => String(x.id) === String(args[1])); if (u) u.ativo = args[0];
    return { rows: [] };
  }
  if (/UPDATE painel_users SET senha_hash/.test(sql)) {
    const u = users.find(x => String(x.id) === String(args[1])); if (u) u.senha_hash = args[0];
    return { rows: [] };
  }
  if (/SELECT id, usuario, papel, ativo/.test(sql)) return { rows: users };
  if (/FROM wa_wifi_change_log/.test(sql)) return { rows: [] };
  return { rows: [{ n: 0 }] };
};

let ok = 0, fail = 0;
function check(cond, desc) { if (cond) { ok++; console.log('  OK   ' + desc); }
  else { fail++; console.log('  FALHA ' + desc); } }

function req(method, path, { cookie, body } = {}) {
  return new Promise((resolve) => {
    const data = body ? JSON.stringify(body) : null;
    const r = http.request({ method, path, host: '127.0.0.1', port: server.address().port,
      headers: Object.assign({ 'Content-Type': 'application/json' },
        data ? { 'Content-Length': Buffer.byteLength(data) } : {},
        cookie ? { Cookie: cookie } : {}) }, (res) => {
      let b = ''; res.on('data', c => b += c);
      res.on('end', () => resolve({ status: res.statusCode,
        cookie: (res.headers['set-cookie'] || [])[0], json: (() => { try { return JSON.parse(b); } catch (e) { return {}; } })() }));
    });
    if (data) r.write(data); r.end();
  });
}

let server;
(async () => {
  server = app.listen(0);

  // login errado
  let r = await req('POST', '/api/login', { body: { usuario: 'chefe', senha: 'errada' } });
  check(r.status === 401, 'senha errada -> 401');

  // login certo devolve cookie
  r = await req('POST', '/api/login', { body: { usuario: 'chefe', senha: 'segredo123' } });
  check(r.status === 200 && r.json.papel === 'admin', 'login admin -> 200 e papel admin');
  const cookieAdmin = r.cookie;
  check(/httponly/i.test(cookieAdmin || ''), 'cookie de sessao e httpOnly');

  // sem cookie, rota protegida barra
  r = await req('GET', '/api/atendimentos');
  check(r.status === 401, 'sem sessao -> 401 nas rotas protegidas');

  // com cookie, passa
  r = await req('GET', '/api/atendimentos', { cookie: cookieAdmin });
  check(r.status === 200, 'com sessao -> 200 em atendimentos');

  // /api/eu reflete o usuario
  r = await req('GET', '/api/eu', { cookie: cookieAdmin });
  check(r.status === 200 && r.json.papel === 'admin', '/api/eu devolve o papel');

  // user comum nao acessa gestao de usuarios
  r = await req('POST', '/api/login', { body: { usuario: 'atendente', senha: 'atende123' } });
  const cookieUser = r.cookie;
  r = await req('GET', '/api/usuarios', { cookie: cookieUser });
  check(r.status === 403, 'user comum -> 403 em /api/usuarios (portao de admin)');

  // admin acessa gestao de usuarios
  r = await req('GET', '/api/usuarios', { cookie: cookieAdmin });
  check(r.status === 200 && Array.isArray(r.json), 'admin -> 200 e lista em /api/usuarios');

  // admin nao pode se autodesativar
  r = await req('POST', '/api/usuarios/1/ativo', { cookie: cookieAdmin, body: { ativo: false } });
  check(r.status === 400, 'admin nao consegue desativar a propria conta');

  // criar um usuario, desativar e reativar - conferindo o efeito de verdade
  r = await req('POST', '/api/usuarios', { cookie: cookieAdmin,
    body: { usuario: 'novato', senha: 'novato123', papel: 'user' } });
  check(r.status === 200, 'admin cria usuario novo');
  const novato = users.find(u => u.usuario === 'novato');

  r = await req('POST', '/api/usuarios/' + novato.id + '/ativo', { cookie: cookieAdmin, body: { ativo: false } });
  check(r.status === 200 && novato.ativo === false, 'DESATIVAR usuario funciona (ativo virou false)');

  r = await req('POST', '/api/usuarios/' + novato.id + '/ativo', { cookie: cookieAdmin, body: { ativo: true } });
  check(r.status === 200 && novato.ativo === true, 'reativar usuario funciona');

  // trocar a senha e conferir que a nova vale (login com a nova)
  r = await req('POST', '/api/usuarios/' + novato.id + '/senha', { cookie: cookieAdmin, body: { senha: 'trocada456' } });
  check(r.status === 200, 'TROCAR senha do usuario retorna ok');
  r = await req('POST', '/api/login', { body: { usuario: 'novato', senha: 'trocada456' } });
  check(r.status === 200, 'login com a nova senha funciona (troca de senha valeu)');
  r = await req('POST', '/api/login', { body: { usuario: 'novato', senha: 'novato123' } });
  check(r.status === 401, 'senha antiga deixou de valer');

  // senha curta e recusada
  r = await req('POST', '/api/usuarios/' + novato.id + '/senha', { cookie: cookieAdmin, body: { senha: '123' } });
  check(r.status === 400, 'senha curta e recusada');

  server.close();
  console.log('\n' + ok + ' passaram, ' + fail + ' falharam');
  process.exit(fail ? 1 : 0);
})();
