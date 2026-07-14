# Railway.com Deployment Guide for License Server

## Prerequisites
- Railway.com account (free tier available)
- GitHub account (optional, but recommended for easy deployment)
- Git installed locally (optional)

---

## Step 1: Prepare Your Project for Railway

### 1.1 Create `Procfile` (if not exists)
Railway needs to know how to start your app. Create a file named `Procfile` in the root directory:

```
web: python license_server/app.py
```

### 1.2 Update `requirements.txt`
Ensure all dependencies are specified. Update with:

```
flask>=3.0.0
cryptography>=42.0.0
gunicorn>=21.0.0
python-dotenv>=1.0.0
```

### 1.3 Create `.railwayignore` (optional)
Prevent unnecessary files from being deployed:

```
__pycache__/
*.pyc
.git
.gitignore
.env.local
license_server/data/*.db
```

### 1.4 Create `runtime.txt`
Specify Python version:

```
python-3.11.7
```

---

## Step 2: Prepare Environment Variables

Create environment variables for Railway. These will be set in the Railway dashboard:

**Required Variables:**
- `FLASK_SECRET_KEY` - Flask session secret (generate a random string)
- `LICENSE_SHARED_SECRET` - Shared secret for HMAC encryption
- `LICENSE_SERVER_PORT` - Port (Railway will set this automatically, default: 5000)
- `FLASK_DEBUG` - Set to "false" for production
- `DAILY_ACCOUNT_LIMIT` - Max accounts per day (default: 6600)

**Optional Variables:**
- `DATABASE_PATH` - Custom database path (default: `data/license_server.db`)

### Generate Secure Secrets (locally):
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```
Run this command twice to get two different secrets.

---

## Step 3: Deploy to Railway

### Option A: Deploy via GitHub (Recommended)

1. **Push code to GitHub:**
   ```bash
   git init
   git add .
   git commit -m "Initial commit: License server"
   git branch -M main
   git remote add origin https://github.com/YOUR_USERNAME/license-server.git
   git push -u origin main
   ```

2. **Connect GitHub to Railway:**
   - Go to [railway.app](https://railway.app)
   - Click "New Project"
   - Select "Deploy from GitHub"
   - Authorize Railway to access your GitHub
   - Select the `license-server` repository
   - Click "Deploy"

3. **Railway will automatically:**
   - Detect Python project
   - Install dependencies from `requirements.txt`
   - Run the app using `Procfile`

### Option B: Deploy via CLI

1. **Install Railway CLI:**
   ```bash
   npm install -g @railway/cli
   ```

2. **Login to Railway:**
   ```bash
   railway login
   ```

3. **Initialize Railway project:**
   ```bash
   cd "d:\my websites\Pixverse Accounts\Organized\Other Pixverse Projects\selling\Script System"
   railway init
   ```

4. **Deploy:**
   ```bash
   railway up
   ```

### Option C: Deploy via Docker (Advanced)

1. **Create `Dockerfile`:**
   ```dockerfile
   FROM python:3.11-slim
   
   WORKDIR /app
   
   COPY requirements.txt .
   RUN pip install --no-cache-dir -r requirements.txt
   
   COPY license_server ./license_server
   
   EXPOSE 5000
   
   CMD ["python", "-m", "flask", "run", "--host=0.0.0.0"]
   ```

2. **Create `.dockerignore`:**
   ```
   __pycache__
   *.pyc
   .git
   .gitignore
   license_server/data/*.db
   .env
   ```

3. **Deploy:**
   ```bash
   railway up
   ```

---

## Step 4: Configure Environment Variables in Railway

1. **Go to your Railway project dashboard**
2. **Navigate to "Variables" tab**
3. **Add the following variables:**

| Key | Value | Example |
|-----|-------|---------|
| `FLASK_SECRET_KEY` | Generate random string | `a1b2c3d4e5f6...` |
| `LICENSE_SHARED_SECRET` | Generate random string | `x9y8z7w6v5...` |
| `FLASK_DEBUG` | false | `false` |
| `DAILY_ACCOUNT_LIMIT` | 6600 | `6600` |
| `LICENSE_SERVER_PORT` | 5000 | (Railway auto-assigns) |

---

## Step 5: Database Persistence

Railway deploys are **ephemeral** by default. To persist the database:

### Option A: Use Railway PostgreSQL Plugin (Recommended)

1. **Add PostgreSQL to your project:**
   - In Railway dashboard, click "Add Service"
   - Select "PostgreSQL"
   - Railway auto-creates connection variables

2. **Modify `models.py` to use PostgreSQL:**
   ```python
   # Replace sqlite3 with psycopg2/SQLAlchemy
   # This requires more significant code changes
   ```

### Option B: Use Railway Volume (Simpler)

1. **In Railway dashboard:**
   - Go to your app service
   - Click "Settings"
   - Scroll to "Volumes"
   - Click "Add Volume"
   - Mount path: `/app/license_server/data`
   - Size: 1GB (sufficient)

2. **This persists the SQLite database across deployments**

### Option C: Use S3-Compatible Storage (Advanced)

For backups and multi-instance deployments:
- Configure Railway's `S3_BUCKET` environment variables
- Modify code to backup/restore databases to S3

---

## Step 6: Connect Domain (Optional)

1. **Go to your Railway project > Settings**
2. **Under "Domain," add your custom domain:**
   - Railway provides: `license-server-production.up.railway.app`
   - Add custom domain: `licenses.yourdomain.com`
   - Configure DNS CNAME record

---

## Step 7: Monitor & Logs

1. **View logs:**
   - Railway dashboard → "Logs" tab
   - Real-time output visible

2. **Set up monitoring:**
   - Enable email notifications for deployment failures
   - Add custom metrics for license usage

---

## Step 8: Database Backup Strategy

### Automated Daily Backups

1. **Create a backup script (`backup.py`):**
   ```python
   import os
   import shutil
   from datetime import datetime
   from pathlib import Path
   
   DB_FILE = Path("license_server/data/license_server.db")
   BACKUP_DIR = Path("backups")
   BACKUP_DIR.mkdir(exist_ok=True)
   
   def backup_database():
       timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
       backup_file = BACKUP_DIR / f"backup_{timestamp}.db"
       shutil.copy2(DB_FILE, backup_file)
       print(f"Backup created: {backup_file}")
       
       # Keep only last 7 backups
       backups = sorted(BACKUP_DIR.glob("backup_*.db"))
       for old_backup in backups[:-7]:
           old_backup.unlink()
       
   if __name__ == "__main__":
       backup_database()
   ```

2. **Set up Railway scheduled job:**
   - Create a separate Railway service for backups
   - Use `railway run python backup.py` on a schedule
   - Or use GitHub Actions to trigger backups

3. **Download backups manually:**
   - Use the dashboard export feature: `/admin/export_db`
   - Download exports periodically and store securely

---

## Step 9: SSL/HTTPS

Railway automatically provides **SSL certificates** for all deployments at no cost using Let's Encrypt.

- HTTPS is **enabled by default**
- Certificates auto-renew
- Your domain is protected

---

## Step 10: Performance Optimization for Railway

### 1. Use Gunicorn (Production Server)

Modify `Procfile`:
```
web: gunicorn --workers 4 --worker-class sync --timeout 30 --bind 0.0.0.0:$PORT license_server.app:app
```

### 2. Enable Caching

Add to `app.py`:
```python
from flask_caching import Cache

cache = Cache(app, config={'CACHE_TYPE': 'simple'})

@app.route('/admin')
@cache.cached(timeout=60)
def admin_dashboard():
    # Your code here
    pass
```

### 3. Optimize Database

- Add indexes to frequently queried columns
- Implement pagination for large datasets
- Use connection pooling

---

## Step 11: Security Checklist

- [ ] Change default admin password immediately
- [ ] Rotate `FLASK_SECRET_KEY` and `LICENSE_SHARED_SECRET` regularly
- [ ] Enable Railway's auto-updates for dependencies
- [ ] Set `FLASK_DEBUG` to `false`
- [ ] Use environment variables for ALL secrets
- [ ] Enable HTTPS (automatic on Railway)
- [ ] Implement rate limiting on API endpoints
- [ ] Regular backup exports
- [ ] Monitor audit logs

---

## Step 12: Troubleshooting

### App Not Starting

1. **Check logs:** Railway dashboard → Logs
2. **Common issues:**
   - Missing dependencies: Update `requirements.txt`
   - Port binding: Ensure `$PORT` variable is used
   - Python version: Check `runtime.txt`

### Database Not Persisting

1. **Verify volume is mounted:**
   - Settings → Volumes → Check `/app/license_server/data`
2. **Check disk space:**
   - Railway free tier: Limited storage
   - Upgrade if needed

### Slow Performance

1. **Check worker count in Procfile**
2. **Enable caching for frequently accessed data**
3. **Upgrade Railway plan for more resources**

---

## Step 13: Example Full Deployment

### Complete Setup Checklist:

```bash
# 1. Update project files
cd "d:\my websites\Pixverse Accounts\Organized\Other Pixverse Projects\selling\Script System"

# 2. Create/update Procfile
echo 'web: gunicorn --workers 2 --timeout 30 --bind 0.0.0.0:$PORT license_server.app:app' > Procfile

# 3. Create runtime.txt
echo 'python-3.11.7' > runtime.txt

# 4. Update requirements.txt with gunicorn
# (add gunicorn>=21.0.0)

# 5. Push to GitHub
git add .
git commit -m "Prepare for Railway deployment"
git push origin main

# 6. Deploy on Railway
railway up
```

### Set Environment Variables:
```
FLASK_SECRET_KEY=<generate-random>
LICENSE_SHARED_SECRET=<generate-random>
FLASK_DEBUG=false
DAILY_ACCOUNT_LIMIT=6600
```

### Add Volume:
- Mount `/app/license_server/data` (1GB)

### Access Application:
```
https://license-server-production.up.railway.app
Admin: admin / admin123 (CHANGE THIS!)
```

---

## Step 14: Post-Deployment Tasks

1. **Change default admin password**
2. **Test API endpoints** with your sell_script client
3. **Export first backup** via dashboard
4. **Monitor logs** for errors
5. **Setup alerts** for failed deployments
6. **Document your deployment** for team reference

---

## Useful Railway Commands

```bash
# View logs
railway logs

# Check service status
railway status

# Restart service
railway restart

# View environment variables
railway variables

# Set variable
railway variables set KEY=VALUE

# Open Railway dashboard
railway open

# Connect to database (if using PostgreSQL)
railway connect
```

---

## Cost Estimate

**Railway Free Tier (Sufficient for most use cases):**
- 500 hours/month of compute
- 5GB egress
- Shared database (PostgreSQL)
- Cost: **$0**

**Estimated upgrade if needed:**
- Pro plan: **$5-20/month** depending on usage
- Scale as traffic grows

---

## Support & Resources

- **Railway Docs:** https://docs.railway.app
- **Flask Deployment:** https://flask.palletsprojects.com/deployment/
- **License Server Docs:** See `README.md` in project root

---

## Next Steps

1. Follow steps 1-12 above
2. Test login at `https://your-railway-app.up.railway.app/admin`
3. Create test licenses and verify API connectivity
4. Set up automated backups
5. Monitor performance and logs

**Deployment complete!** 🚀
