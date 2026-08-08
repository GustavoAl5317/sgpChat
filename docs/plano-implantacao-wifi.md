# Troca de Wi-Fi automática — o que preciso de vocês e o que vou entregar

**Situação em uma frase:** a TSMX confirmou que o servidor TR-069 (ACS) é
infraestrutura do provedor, não do SGP. Decidimos implantar o **GenieACS**
(gratuito, e a própria TSMX confirmou ser o mais usado pelos clientes deles)
direto para atender toda a base, na mesma estrutura onde já roda o
atendimento no WhatsApp.

Este documento separa o que está sob meu controle do que depende de vocês —
e, para cada pedido, explico por que preciso dele, para que a decisão de
priorizar (ou não) seja informada.

---

## O que eu vou entregar

Isto não depende de mais nenhuma resposta de vocês — já está em andamento:

- Instalar e proteger o servidor GenieACS na infraestrutura atual: acesso
  criptografado (HTTPS), autenticação por token, backup automático diário.
- Integrar esse servidor ao SGP assim que tiver a confirmação de formato
  (pedido 1, abaixo).
- Escrever a especificação técnica exata — os parâmetros e valores — para o
  time de rede de vocês usar ao configurar os equipamentos.
- Testar com um grupo pequeno antes de qualquer expansão, e só liberar para
  mais clientes depois de confirmar que funciona de ponta a ponta.
- Manter funcionando, para quem não puder usar essa automação (equipamento
  incompatível ou cliente com roteador próprio — ver pedido 5), o caminho que
  **já está no ar hoje**: o assistente recolhe o pedido pelo WhatsApp e abre
  um chamado no SGP para a equipe de vocês aplicar manualmente. Ninguém fica
  sem atendimento por causa dessas exceções.

---

## O que eu preciso de vocês

### 1. TSMX — formato exato do JSON de integração

**Preciso que:** alguém pergunte ao suporte da TSMX qual é o JSON esperado no
campo "Parâmetros JSON" ao cadastrar um Gerenciador de CPE do tipo GenieACS.

**Por que preciso:** esse campo mostra sempre o exemplo do Anlix
(`flashman.anlix.io`) e não muda ao selecionar GenieACS no menu — é um texto
fixo, não um modelo por gerenciador. Eu tenho um formato provável, baseado no
padrão que outros provedores usam, mas **é um palpite**. Como isso vai
diretamente para produção, prefiro confirmar do que arriscar uma
configuração errada tocando o sistema real.

**Se não vier:** eu sigo com o palpite documentado e ajusto por tentativa e
erro. Funciona, mas cada tentativa errada é uma rodada de teste perdida —
mais rápido com a resposta certa da fonte.

### 2. TSMX — IP de saída do SGP

**Preciso que:** perguntem também qual é o IP (ou faixa de IPs) que o SGP usa
para fazer as chamadas de saída para os servidores integrados.

**Por que preciso:** o servidor GenieACS já fica protegido por senha e
criptografia, mas como ele controla o roteador de todos os assinantes, quero
uma segunda camada: liberar o acesso só para o IP de onde o SGP realmente
chama, em vez de aceitar conexão de qualquer lugar da internet.

**Se não vier:** o sistema funciona igual, só fica com uma camada de proteção
a menos. Não é bloqueante, mas é recomendação de segurança forte.

### 3. Um domínio (subdomínio) apontando para o servidor

**Preciso que:** um subdomínio de vocês (por exemplo `acs.rcnet.com.br`)
aponte para o IP do servidor onde o GenieACS vai rodar.

**Por que preciso:** para emitir um certificado de segurança reconhecido
(gratuito, via Let's Encrypt) para a conexão entre o SGP e o servidor. Sem
domínio, uso um certificado provisório — alguns sistemas recusam ou alertam
sobre esse tipo de certificado quando a conexão passa pela internet, que é o
caso aqui.

**Se não vier:** sigo com o certificado provisório até haver domínio. Não
impede o funcionamento, mas não é o ideal para deixar em definitivo.

### 4. Confirmação: as ONUs de vocês suportam TR-069?

**Preciso que:** o time técnico confirme se os modelos de ONU usados na base
têm o protocolo TR-069 no firmware — isso costuma variar por fabricante e
até por linha de equipamento (mais comum em modelos com roteador integrado
de marcas como Huawei, ZTE, Fiberhome, Datacom).

**Por que preciso:** essa é uma capacidade do próprio equipamento, não uma
configuração. Se o equipamento não tiver esse recurso, não existe ajuste no
servidor, no SGP ou na OLT que resolva — é limitação de hardware.

**Se não vier / se a resposta for "não em todo mundo":** não é motivo para
não seguir. Só significa que o módulo de troca automática de Wi-Fi
provavelmente nunca vai cobrir 100% da base — e, para essa parcela, o
atendimento continua pelo caminho de chamado (item já entregue).

### 5. Levantamento: quantos clientes usam roteador próprio?

**Preciso que:** identifiquem, mesmo que aproximadamente, quantos contratos
têm a ONU em modo *bridge* com um roteador separado do cliente, em vez do
Wi-Fi da própria ONU.

**Por que preciso:** nesses casos, mesmo com tudo funcionando perfeitamente,
o servidor controlaria o Wi-Fi da ONU — que o cliente não usa. O roteador
dele fica fora do alcance dessa automação, e não há solução técnica para
isso a partir do nosso lado.

**Se não vier:** eu não consigo saber de antemão quais contratos vão "falhar
silenciosamente" quando a automação tentar agir. Prefiro que esses contratos
sejam identificados e mantidos deliberadamente no atendimento por chamado, a
descobrir isso pelos clientes reclamando.

### 6. Provisionar as ONUs na OLT — o passo decisivo

**Preciso que:** o time de rede de vocês, com acesso à OLT, configure em
cada equipamento (ou no perfil aplicado a eles) o endereço do servidor
GenieACS. Vou entregar a especificação exata assim que o servidor estiver no
ar — são poucos parâmetros, mas é execução de rede, e só quem tem acesso à
OLT de vocês consegue fazer.

**Por que preciso:** sem isso, o servidor pode estar perfeito e o SGP
perfeitamente integrado, e nada funciona — nenhum equipamento vai saber que
esse servidor existe. É o elo que conecta tudo.

**Se não vier:** o projeto para exatamente aqui. É o único item desta lista
sem alternativa — os outros têm contorno (chamado, certificado provisório,
tentativa e erro); este não tem.

### 7. Informação: o provisionamento pode ser feito em lote?

**Preciso que:** perguntem ao time de rede se a configuração acima pode ser
aplicada de uma vez a vários equipamentos (por um perfil/template na OLT) ou
se precisa ser feita individualmente.

**Por que preciso:** isso define o ritmo do rollout. Se for possível em
lote, dá para expandir rápido depois do teste inicial. Se for individual,
faz mais sentido planejar por etapas — por exemplo, priorizando quem mais
liga pedindo troca de Wi-Fi.

**Se não vier:** eu assumo que é individual e planejo por etapas pequenas,
que é o caminho mais seguro de qualquer forma — mesmo que dê para fazer em
lote, prefiro validar num grupo pequeno antes de aplicar na base toda.

---

## Resumo — perguntas para encaminhar

**Para a TSMX:**
1. Qual o JSON exato esperado no Gerenciador de CPE para GenieACS?
2. Qual o IP de saída do SGP, para liberarmos no firewall?

**Para o time técnico de vocês:**
3. Existe um subdomínio disponível para apontar para o servidor?
4. As ONUs da base suportam TR-069? Depende do modelo — vocês sabem quais
   são os mais usados?
5. Temos ideia de quantos clientes usam roteador próprio (ONU em bridge)?
6. Alguém consegue configurar o TR-069 na OLT quando eu tiver o servidor
   pronto?
7. Isso é feito em lote (perfil) ou equipamento por equipamento?

---

## O que já está no ar, hoje, sem depender de nada disso

A troca de Wi-Fi pelo WhatsApp já funciona no modelo por chamado: o cliente
se identifica, escolhe o que quer alterar, confirma, e o assistente abre uma
ocorrência no SGP para a equipe executar — com a identidade já validada e os
dados prontos, sem precisar de ligação. Continuará funcionando assim para
sempre nos casos do item 4/5 acima, e para os demais até a automação estar
validada.
