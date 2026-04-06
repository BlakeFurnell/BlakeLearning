# r-gen-app

R Studio code generation app powered by Ollama and FastAPI.

## Getting Started

1. **Create a virtual environment and install dependencies**

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Configure environment variables**

   ```bash
   cp .env.example .env
   # Edit .env with your Ollama instance details
   ```

3. **Run the development server**

   ```bash
   uvicorn main:app --reload
   ```

4. **Open the app**

   Navigate to [http://localhost:8000](http://localhost:8000)
