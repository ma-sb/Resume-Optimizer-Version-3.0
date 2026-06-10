# Resume Polisher 3.0

AI-powered resume evaluation, matching, optimization, and interview copilot.

## Features

1. **Search job postings** — search for live job listings by position directly in the app
2. **Read job descriptions** — paste any job posting into the app
3. **Load resumes** — reads all `.docx` files from a configurable folder
4. **Match & score** — ranks every resume against the job and recommends the best fit
5. **Improvement suggestions** — identifies weak bullet points and rewrites them with relevant keywords
6. **Optimize & export** — generates a fully tailored resume and exports it as a clean PDF  
   (`FirstName_LastName_Resume_Company.pdf`)

## Quick start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Drop your .docx resumes into the resumes/ folder

# 3. Run the app
streamlit run app.py
```

Enter your **LLM API key** and **Adzuna credentials** in the sidebar when prompted.

## API keys

| Key | Where to get it |
|-----|----------------|
| OpenAI / Gemini / Claude | Respective provider dashboards |
| Adzuna App ID & API Key | [developer.adzuna.com](https://developer.adzuna.com) |

No credentials are stored by the app — all keys are entered at runtime and stay in your browser session only.

## Project structure

```
Prototype/
├── app.py               # Streamlit UI
├── cli.py               # Typer CLI (match / improve / optimize / export)
├── JobFetch.py          # Job search via Adzuna API
├── core/
│   ├── reader.py        # .docx parsing
│   ├── matcher.py       # AI scoring, improvements, optimization
│   └── exporter.py      # PDF generation
├── resumes/             # Place .docx resumes here
├── output/              # Exported PDFs land here
└── requirements.txt
```
