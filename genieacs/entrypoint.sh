#!/bin/sh
# Sobe os quatro servicos do GenieACS e derruba o container se qualquer um
# morrer. Sem isso o container fica "de pe" com o CWMP morto, a ONU para de
# reportar e ninguem percebe ate alguem testar na mao.
set -e

pids=""
for s in genieacs-cwmp genieacs-nbi genieacs-fs genieacs-ui; do
  echo "==> iniciando $s"
  "$s" &
  pids="$pids $!"
done

# Espera qualquer um terminar. Como todos deveriam rodar para sempre, qualquer
# retorno aqui e falha - encerra os outros e deixa o restart policy agir.
wait -n $pids
echo "[x] um servico do GenieACS encerrou. Derrubando o container."
kill $pids 2>/dev/null || true
exit 1
