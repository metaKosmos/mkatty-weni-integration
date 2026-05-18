# mkatty-weni-integration

Integração da API de Virtual Try-On da **mKatty** (metaKosmos) com o chatbot **Weni by VTEX**.

O usuário manda uma foto + um ou mais SKUs de produtos Natura pelo WhatsApp e recebe de volta as prévias da maquiagem aplicada, em forma de carrossel.

## Stack

- Python 3.12 + `weni-agents-toolkit` (tool)
- YAML (definição do agente)
- API externa: mKatty (`/pulpo/vto`)
- Deploy via Weni-CLI

## Estrutura

```
.
├── agent_definition.yaml       # manifesto do agente
└── tools/
    └── apply_makeup/
        ├── main.py             # a tool em si
        └── requirements.txt
```

## Contrato da API mKatty

```
POST https://mkatty.metakosmoslab.com/pulpo/vto
Content-Type: application/json

Body:
{
  "photoBase64": "<base64 da foto>",
  "productCode": "NATBRA-90905"
}

Response 200 (sucesso):
{
  "success": true,
  "productCode": "NATBRA-90905",
  "projectSlug": "vto-natura",
  "variantId": "<uuid>",
  "product": "Batom Matte Faces",
  "category": "Batom",
  "color": "#A45A57",
  "mimeType": "image/png",
  "imageUrl": "https://storage.mk3dlabs.com/media/pulpo/.../resultado.png"
}

Response 500 (slug inválido):
{
  "success": false,
  "error": "SDK error: No variants were found with the slugs ..."
}
```

A tool consome essa rota uma vez por SKU (em paralelo) e usa o `imageUrl` retornado pra montar a mensagem no WhatsApp.

## Deploy

```bash
pip install weni-cli
weni login
weni project use <uuid-do-projeto>
weni project push agent_definition.yaml
```
