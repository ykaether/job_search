# Render Deploy Notes

## Files added for Render

- `requirements.txt`
- `render.yaml`

## Recommended deploy flow

1. Push this folder to a GitHub repository.
2. In Render, create a new Blueprint or Web Service from the repository.
3. Confirm Render detects:
   - Build command: `pip install -r requirements.txt`
   - Start command: `streamlit run app.py --server.address 0.0.0.0 --server.port $PORT`
4. Set `ANTHROPIC_API_KEY` in Render environment variables.

## Important caveat

This app currently stores user data in local JSON files:

- `data.json`
- `users.json`
- `sessions.json`
- `data_*`

On Render free web services, local file writes are not a durable database.
Data may be lost on restart, redeploy, or instance replacement.

So this setup is good for:

- quick public testing
- checking speed vs ngrok
- validating basic deployment

It is not yet suitable for reliable long-term production use without moving data storage off local JSON.
