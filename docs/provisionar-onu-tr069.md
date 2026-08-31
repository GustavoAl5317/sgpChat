# Apontar uma ONU para o servidor de gerenciamento (TR-069)

**Para:** equipe técnica da RCNet
**Não entregue este documento ao assinante** — o procedimento exige a senha
administrativa da ONT, e um campo errado derruba a conexão da casa.

---

## Para que serve

Hoje, trocar o nome ou a senha do Wi-Fi de um assinante exige alguém entrar no
aparelho — por telefone guiando o cliente, ou com visita.

Depois deste procedimento, o aparelho passa a conversar com um servidor da
RCNet. A partir daí a troca pode ser feita à distância: pelo atendimento
automático no WhatsApp, o próprio assinante resolve em menos de um minuto, sem
ligar para ninguém.

É configuração feita **uma vez por aparelho**. Depois disso vale para sempre —
inclusive para diagnóstico remoto e outras configurações no futuro.

---

## Antes de começar

Tenha em mãos:

| item | valor |
|---|---|
| Endereço do servidor | `http://179.197.226.140:7547/` |
| Senha administrativa da ONT | a que a RCNet usa (não é a senha do cliente) |

E confirme que o aparelho **não está em modo bridge**. Em bridge, o Wi-Fi que o
assinante usa vem de outro roteador, e este procedimento não tem efeito para
ele.

---

## Passo a passo

**1. Conecte-se à rede do aparelho** — por cabo ou pelo Wi-Fi da casa.

**2. Abra o navegador** no endereço de administração da ONT:

- Huawei: `192.168.100.1`
- ZTE: `192.168.1.1`

**3. Entre com o usuário administrativo.** Não é o login que aparece na
etiqueta do aparelho — aquele é o do cliente e não mostra estas telas.

**4. Localize a configuração de TR-069.** O nome do menu muda conforme o
fabricante e a versão do firmware. Procure por:

- **TR-069** ou **TR069**
- **Gerenciamento remoto** / *Remote Management*
- Em alguns modelos fica dentro de **WAN**, como um tipo de conexão

Se não encontrar, tire uma foto da tela de menus antes de mexer em qualquer
coisa.

**5. Preencha os campos:**

| campo | valor |
|---|---|
| ACS URL / Endereço do servidor | `http://179.197.226.140:7547/` |
| ACS Username / Usuário | qualquer valor (ex: `acs`) |
| ACS Password / Senha | qualquer valor (ex: `acs`) |
| Periodic Inform / Informe periódico | **habilitado** |
| Inform Interval / Intervalo | `300` |
| Connection Request User / Senha | defina e **anote** |

O usuário e senha do ACS podem ser qualquer coisa: o servidor não os exige.
Já os de *Connection Request* são como o servidor acorda o aparelho — anote,
porque eles precisam ser iguais em todos os aparelhos para o processo virar
rotina depois.

**6. Salve.** O aparelho pode reiniciar a conexão por alguns segundos.

---

## Como saber se funcionou

Em até 5 minutos o aparelho aparece na lista do servidor. Quem confirma isso é
quem tem acesso ao servidor — avise a pessoa responsável assim que salvar, e
tenha o **número de série** do aparelho à mão (fica na etiqueta, começa com
`HWTC` nos Huawei e `ZTEG` nos ZTE).

Se não aparecer, os suspeitos, nesta ordem:

1. Um campo salvo errado — o endereço é o mais comum, confira o `http://` e a
   porta `:7547`.
2. O informe periódico ficou desabilitado.
3. O firmware do aparelho não aceita TR-069 configurado à mão. Acontece em
   modelos que vieram travados de outro provedor. Nesse caso não há o que
   fazer nessa ONT — anote o modelo e passe para a próxima.

---

## Por que isto está sendo feito um a um

Este procedimento manual serve para **provar o caminho** em um aparelho antes
de qualquer coisa em escala. Depois de funcionar uma vez, a mesma configuração
passa a ser aplicada pela OLT, sem entrar em aparelho nenhum — e aí a base
inteira é feita em lote.

Fazer manualmente na base toda não é o plano. É só o primeiro passo.
