# 3x-ui Subscription Modifier

A small FastAPI/Uvicorn service that:

- accepts **GET only** at `/{BASE_SUB_URL}/{SUB_ID}`
- fetches the upstream subscription from `http://127.0.0.1:{BASE_SUB_PORT}/{BASE_SUB_URL}/{SUB_ID}`
- decodes the upstream base64 payload
- rewrites each supported subscription link
- returns the modified subscription as base64 text

## What it changes

- host/IP to `TARGET_HOST`
- port to `TARGET_PORT`
- adds `alpn=h2`
- keeps `host=` present even when empty
- changes the fragment / display name to `TARGET_LINK_NAME` (you can set it to a callable instead of just a fixed string, for example cutting off the comment)
- optionally sets `sni=` when `TARGET_SNI` is configured

## Run

```bash
cp .env.example .env
pip install -r requirements.txt
python main.py
```

Or:

```bash
uvicorn main:app --host 127.0.0.1 --port 8080
```

## Notes

- The reusable parameter helpers are `add_query_param`, `remove_query_param`, and `set_query_param`.
- `set_query_param()` is implemented as remove-then-add.
- The service is designed to sit behind NGINX and receive plain HTTP from localhost.