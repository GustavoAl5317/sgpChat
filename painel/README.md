# painel — o painel de atendimentos da equipe

Uma página web onde a equipe do provedor vê o que o bot fez: histórico de
atendimentos, consulta de cliente, conversas e um resumo com números. Login por
usuário e senha; **todos veem tudo, só o admin gerencia contas**.

## O que ele lê

- **Histórico de atendimentos** — a tabela `wa_wifi_change_log`, que o bot já
  grava a cada troca de senha, boleto, chamado ou diagnóstico: quem, quando,
  contrato e resultado.
- **Consulta de cliente** — digita CPF/CNPJ e o painel pergunta ao SGP (mesmas
  credenciais do bot) os contratos daquela pessoa, e junta o histórico dela no
  atendimento automático.
- **Resumo** — totais por tipo, atendimentos do dia, últimos 7 dias e quantas
  OS de habilitação de TR-069 (Huawei) foram abertas.
- **Conversas** — o texto de cada diálogo, da tabela `wa_messages`. Essa tabela
  só passa a ser preenchida quando o bot for atualizado para registrar as
  mensagens; antes disso a aba fica vazia (o histórico de conversas começa a
  acumular a partir daí, não dá para recuperar o que já passou).

O painel **nunca escreve** nas tabelas do bot. A única tabela que ele mantém é a
`painel_users` (as contas de acesso).

## Segurança

- Senha nunca em claro: só o hash **bcrypt** fica no banco.
- Sessão por cookie **httpOnly** assinado (JWT), expira em 12 h.
- O portão de admin é verificado no servidor, não só na tela: um usuário comum
  que chame a API de gestão de contas recebe 403.
- O admin não consegue desativar a própria conta (não dá para se trancar do
  lado de fora).

## Configuração

No `.env` da raiz (ver `.env.example`):

| variável | para quê |
|---|---|
| `PAINEL_DOMAIN` | domínio onde a equipe acessa |
| `PAINEL_JWT_SECRET` | assina o cookie — `openssl rand -hex 32` |
| `PAINEL_ADMIN_USER` / `PAINEL_ADMIN_PASS` | primeira conta admin (só na primeira subida) |
| `POSTGRES_*`, `SGP_*` | reaproveitadas do bot |

A primeira conta admin nasce dessas variáveis **só se ainda não houver nenhuma
conta**. Depois disso, contas se criam dentro do painel, e a senha inicial deve
ser trocada.

## Subir

```bash
docker compose up -d --build painel
bash publicar-no-traefik.sh   # publica painel.<seu-dominio> no Traefik
```

`https://painel.<seu-dominio>` abre a tela de login.

## Testar

```bash
node painel/test-painel.js
```

Sobe o servidor com um banco de mentira e exercita o que tem risco: login,
cookie de sessão, e o portão de admin (usuário comum barrado na gestão de
contas). Não precisa de Postgres.
