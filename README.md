# AI Auto Script

A browser-based script follower for live A/V production. Upload a script (PDF, DOCX, XLSX, TXT, or image), and the app listens to program audio in real time, finds where the speaker is in the script, and auto-scrolls to follow along.

## How It Works

1. Upload your script document
2. Select your audio input (mic or audio interface)
3. Click **Listen** — the app transcribes speech and highlights the current position in the script
4. The script auto-scrolls as the speaker progresses

Speech-to-text is powered by **Deepgram Nova-3**. You'll need a free Deepgram account — it includes $200 of credit per year (roughly 550 hours of audio). [Sign up at deepgram.com](https://deepgram.com)

---

## Requirements

- **macOS** (tested on macOS 14+)
- **Python 3.9+**
- **Node.js 18+** and **npm**
- **Tesseract** (for OCR on image-based scripts)

---

## Installation

### 1. Install system dependencies

```bash
# Install Homebrew if you don't have it
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Install Python, Node.js, and Tesseract
brew install python@3.9 node tesseract
```

If you already have Python 3.9+ and Node.js 18+ installed, you just need Tesseract:

```bash
brew install tesseract
```

### 2. Clone the repository

```bash
git clone https://github.com/dowa-hub/ai-auto-script.git
cd ai-auto-script
```

### 3. Run the start script

```bash
chmod +x start.sh
./start.sh
```

This will:
- Create a Python virtual environment
- Install all backend dependencies
- Install all frontend dependencies
- Start both servers
- Open your browser to `http://localhost:5173`

That's it — you're ready to go.

---

## Manual Setup (if start.sh doesn't work)

### Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn main:app --reload --port 8000
```

### Frontend (in a separate terminal)

```bash
cd frontend
npm install
npm run dev
```

Then open `http://localhost:5173` in your browser.

---

## Configuration

Optionally edit `backend/.env`:

| Variable | Description |
|----------|-------------|
| `DEEPGRAM_API_KEY` | Your Deepgram API key (or paste it in the sidebar at runtime) |
| `ANTHROPIC_API_KEY` | Optional — enables Claude fallback for hard tracking recoveries |

---

## Supported File Formats

| Format | How it's displayed |
|--------|-------------------|
| PDF | Native PDF rendering (PDF.js) |
| DOCX / DOC | Converted to HTML with formatting preserved |
| XLSX / XLS | Tables rendered as HTML |
| TXT | Plain text |
| JPG / PNG | Image with OCR text extraction |

---

## Usage Tips

- The app works best when the speaker is reading directly from the script
- Click any line in the script to manually jump there
- The script is cached locally, so it survives server restarts
- For production use, connect your audio interface output (program audio) rather than a room mic

---

## Shutting Down

Press `Ctrl+C` in the terminal where `start.sh` is running, or:

```bash
pkill -f "uvicorn main:app"
pkill -f "vite"
```

---

## Starting Back Up

```bash
cd ai-auto-script
./start.sh
```
