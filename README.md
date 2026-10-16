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

### 4. Environment Configuration
Create a `.env` file in the project root directory:
```bash
# Create .env file
touch .env  # On Windows: New-Item .env -ItemType File
```

Add your Edstem credentials to the `.env` file. An API token is recommended:
```env
ED_API_TOKEN=your_api_token
```

If `ED_API_TOKEN` is not set, the app falls back to logging in with your email and password:
```env
ED_EMAIL=your_email@example.com
ED_PASSWORD=your_password
```

When `ED_API_TOKEN` is set it always takes precedence, and `ED_EMAIL`/`ED_PASSWORD` are ignored.