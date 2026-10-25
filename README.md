# HTML Decoder Telegram Bot

## Render setup
1. Extract the ZIP.
2. Upload the files inside the ZIP (not the ZIP itself) to the root of a GitHub repository.
3. Create a Render Web Service connected to that repository.
4. Build command: `pip install -r requirements.txt`
5. Start command: `python app.py`
6. In Render Environment, add `BOT_TOKEN` with your Telegram bot token.
7. Deploy.

The bot supports HTML/HTM/JS/MJS/CJS/TXT/JSON/XML/CSS, common HTML entity/URL/JavaScript unicode and hex decoding, some Base64 text decoding, basic formatting, and static analysis. It never executes uploaded code.

Limitations: this cannot guarantee reversing custom encryption, virtual-machine obfuscation, or missing-key protected files.
