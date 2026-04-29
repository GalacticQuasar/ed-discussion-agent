# ed-discussion-agent

## Setup Instructions

### 1. Clone the Repository
```bash
git clone https://github.com/GalacticQuasar/ed-discussion-agent.git
cd ed-discussion-agent
```

### 2. Create Virtual Environment
```bash
# Create virtual environment
python -m venv .venv

# Activate virtual environment
# On Windows:
.venv\Scripts\activate
# On macOS/Linux:
source .venv/bin/activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Start the Server
```bash
python main.py
```

The server runs on `http://0.0.0.0:8000`. Open it in your browser and log in with your Edstem credentials on the login page.