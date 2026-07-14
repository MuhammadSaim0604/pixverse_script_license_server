# Railway Deployment Quick Start

## 5-Minute Setup

### 1. Create Environment Variables

Generate two random secrets:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### 2. Push to GitHub

```bash
cd "d:\my websites\Pixverse Accounts\Organized\Other Pixverse Projects\selling\Script System"
git add .
git commit -m "Add database export/import features and Railway config"
git push origin main
```

### 3. Deploy on Railway

**Option A - Via GitHub (Easiest):**
1. Go to https://railway.app
2. Click "New Project"
3. Select "Deploy from GitHub"
4. Choose your repository
5. Click "Deploy"

**Option B - Via CLI:**
```bash
npm install -g @railway/cli
railway login
railway init
railway up
```

### 4. Configure on Railway Dashboard

Set these environment variables:
```
FLASK_SECRET_KEY = (your-secret-1)
LICENSE_SHARED_SECRET = (your-secret-2)
FLASK_DEBUG = false
DAILY_ACCOUNT_LIMIT = 6600
```

### 5. Add Volume for Database Persistence

1. Go to your app service
2. Settings → Volumes
3. Add Volume:
   - Mount: `/app/license_server/data`
   - Size: 1GB

### 6. Access Your App

```
https://your-app-name.up.railway.app/admin
Default user: admin / admin123 (CHANGE THIS!)
```

---

## Project Files Ready for Deployment

✅ **New/Updated Files:**
- `Procfile` - Railway startup configuration
- `runtime.txt` - Python version specification
- `.railwayignore` - Files to exclude from deployment
- `Dockerfile` - Alternative Docker deployment
- `RAILWAY_DEPLOYMENT.md` - Full deployment guide
- `DATABASE_EXPORT_IMPORT_GUIDE.md` - Database feature guide
- Updated `requirements.txt` - Added gunicorn

✅ **New Features:**
- `/admin/export_db` - Export database as SQLite file
- `/admin/export_db_json` - Export as JSON
- `/admin/import_db` - Import/restore database
- `templates/import_db.html` - Import UI

✅ **Server Code:**
- Updated `app.py` - New routes and dependencies
- `models.py` - Unchanged (compatible)
- Templates - Updated dashboard with export/import buttons

---

## New Features: Export/Import Database

### Export Database (SQLite format)
```
Dashboard → "Database Backup" → "Export (.db)"
Downloads: license_server_backup_YYYYMMDD_HHMMSS.db
```

### Export Database (JSON format)
```
Dashboard → "Database Backup" → "Export (JSON)"
Downloads: license_server_backup_YYYYMMDD_HHMMSS.json
```

### Import Database
```
Dashboard → "Database Backup" → "Import Backup"
- Select backup file
- Confirm action
- System creates pre-import backup automatically
```

**Benefits:**
- ✅ Easy daily backups
- ✅ Safe restore with automatic backup
- ✅ Audit logged for security
- ✅ Multiple export formats

---

## Post-Deployment Checklist

- [ ] Change default admin password
- [ ] Test login: `/admin/login`
- [ ] Test API: `/api/ping`
- [ ] Export first backup: `/admin/export_db`
- [ ] Verify database persists (add volume)
- [ ] Set up custom domain (optional)
- [ ] Enable email notifications
- [ ] Review audit logs: `/admin/audit`
- [ ] Test with your sell_script client
- [ ] Document credentials securely

---

## Useful Links

| Resource | URL |
|----------|-----|
| Railway Dashboard | https://railway.app/dashboard |
| Full Deployment Guide | See `RAILWAY_DEPLOYMENT.md` |
| Export/Import Guide | See `DATABASE_EXPORT_IMPORT_GUIDE.md` |
| Flask Docs | https://flask.palletsprojects.com |
| Railway Docs | https://docs.railway.app |

---

## Common Commands

```bash
# View deployment logs
railway logs

# Restart service
railway restart

# View environment variables
railway variables

# Set variable
railway variables set KEY=VALUE

# Open in browser
railway open

# View project info
railway status
```

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| App won't start | Check logs: `railway logs` |
| Database not persisting | Add volume: Settings → Volumes |
| Can't login | Verify `FLASK_SECRET_KEY` is set |
| API calls failing | Check `LICENSE_SHARED_SECRET` matches sell_script |
| Slow performance | Reduce worker count in Procfile |
| Build failing | Check `requirements.txt` dependencies |

---

## Next Steps

1. ✅ Read full guide: `RAILWAY_DEPLOYMENT.md`
2. ✅ Deploy to Railway (5 min)
3. ✅ Set environment variables
4. ✅ Add database volume
5. ✅ Change admin password
6. ✅ Test all features
7. ✅ Set up automated backups
8. ✅ Configure monitoring/alerts

---

## Support

Need help? Check:
1. `RAILWAY_DEPLOYMENT.md` - Full deployment guide
2. `DATABASE_EXPORT_IMPORT_GUIDE.md` - Feature guide
3. Railway logs - `/admin/audit` 
4. Contact: Muhammad Saim (Developer)

---

**Status:** ✅ Ready for deployment!
