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

## Deploy

```bash
pip install weni-cli
weni login
weni project use <uuid-do-projeto>
weni project push agent_definition.yaml
```
