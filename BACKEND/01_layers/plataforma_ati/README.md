# Layer compartilhada

`python/plataforma_ati.py` concentra validação, autorização e helpers de dados.
`python/notifications/` implementa os provedores de e-mail, SMS, Telegram e push.
Somente esse código próprio permanece na pasta de fontes.

## Dependências e build

As dependências diretas estão em [requirements.txt](requirements.txt).
As versões de Bleach, Requests e Firebase Admin foram reconstruídas a partir
dos metadados presentes no ZIP original; não representam uma atualização para
as versões mais recentes. O SDK boto3 passa a ser empacotado junto para evitar
misturar suas dependências com as do SDK gerenciado pelo runtime.

O boto3 usa uma faixa de versões e as dependências transitivas não estão
travadas. Portanto, este manifesto permite **reconstruir** a layer, mas ainda
não garante builds idênticos. Uma revisão de dependências/lockfile deve ser
feita separadamente, com validação no runtime de destino.

Na raiz do repositório, com Docker e SAM CLI disponíveis:

```powershell
python scripts/generate-local-template.py
sam build PlataformaAtiLayerLocal --template-file template.local.yaml --config-env sandbox --use-container --no-cached --build-dir .aws-sam/layer-build
```

O `Makefile` desta pasta copia o código próprio e instala as dependências em:

```text
.aws-sam/layer-build/PlataformaAtiLayerLocal/python/
```

O build deve usar Linux, Python 3.14 e x86_64, conforme `template.yaml`.
Não reutilize binários de um ambiente Python 3.11 nem de Windows.
A estrutura do ZIP precisa conter `python/` na raiz, como descrito na
[documentação AWS de layers Python](https://docs.aws.amazon.com/lambda/latest/dg/python-layers.html).
O build com `BuildMethod: makefile` segue o
[contrato do SAM](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/building-layers.html).

## Publicação

`scripts/update-layer.ps1 -Stage sandbox` constrói a layer, publica uma versão
e atualiza a configuração das funções. **Isso altera recursos reais.**
O script exige funções existentes e substitui a lista de layers de cada função;
não o use sem revisão se houver layers adicionais.

Depois de publicar, persista o ARN com a versão em `LayerArn` no ambiente correto
do `samconfig.toml`. Caso contrário, um deploy SAM seguinte pode voltar à layer
anterior. Para a primeira instalação, publique apenas o ZIP antes de criar a
aplicação, sem tentar atualizar funções inexistentes.

O build real em Docker e a publicação devem ser verificados no ambiente de
operação; testes unitários de estrutura não substituem essa validação.
