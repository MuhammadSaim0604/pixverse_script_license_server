# License Server Database Export/Import Guide

## Overview

The license server now includes two new features for database management:

### 1. **Export Database** ✅
Export your entire database as:
- **SQLite file (.db)** - For direct backup/restore
- **JSON format** - For inspection, archival, or data migration

### 2. **Import Database** ✅
Restore a previously exported database:
- **Automatic backup** - Current database is backed up before import
- **Safe restore** - Validates SQLite format before importing
- **Audit logged** - All imports are logged for security

---

## Features

### Export (.db file)
```
Location: /admin/export_db
Features:
✓ One-click download
✓ Automatic timestamp in filename
✓ Preserves all tables and data
✓ Audit logged
```

### Export (JSON format)
```
Location: /admin/export_db_json
Features:
✓ Human-readable format
✓ Easy inspection of data
✓ Good for archival
✓ Useful for data analysis
```

### Import Database
```
Location: /admin/import_db
Features:
✓ Automatic pre-import backup
✓ File format validation
✓ Prevents data corruption
✓ Audit logged with backup filename
```

---

## Usage Instructions

### To Export Your Database

1. **Login to admin dashboard**
   - Go to: `http://localhost:5000/admin`
   - Login with admin credentials

2. **Scroll to "Database Backup" section** (bottom right)

3. **Click one of:**
   - `Export (.db)` - Standard SQLite backup file
   - `Export (JSON)` - Text-based JSON export

4. **Browser downloads the file** with timestamp:
   - `license_server_backup_20240714_143022.db`
   - `license_server_backup_20240714_143022.json`

5. **Store safely** - Keep backups in a secure location

---

### To Import a Database

1. **Navigate to import page**
   - Click `Import Backup` button on dashboard
   - Or go to: `/admin/import_db`

2. **Select backup file**
   - Click file input or drag & drop
   - Only accepts `.db`, `.sqlite`, `.sqlite3` files

3. **Confirm action**
   - Check the acknowledgment box
   - You understand data will be replaced

4. **Click "Import Database"**

5. **System will:**
   - ✓ Validate the file format
   - ✓ Create pre-import backup: `license_server_backup_pre_import_*.db`
   - ✓ Import the database
   - ✓ Show success message with backup filename

---

## Backup Strategy

### Daily Backups (Recommended)

**Option 1: Manual Export (via Dashboard)**
```
• Export database daily at end of day
• Download and store in cloud storage
• Use version control (e.g., Google Drive, OneDrive)
```

**Option 2: Automated Script**
```python
# backup.py
import shutil
from datetime import datetime
from pathlib import Path

DB_FILE = Path("license_server/data/license_server.db")
BACKUP_DIR = Path("backups")
BACKUP_DIR.mkdir(exist_ok=True)

def backup():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = BACKUP_DIR / f"backup_{timestamp}.db"
    shutil.copy2(DB_FILE, backup_file)
    
    # Keep last 30 backups
    for old in sorted(BACKUP_DIR.glob("backup_*.db"))[:-30]:
        old.unlink()
    
    print(f"✓ Backup: {backup_file}")

if __name__ == "__main__":
    backup()
```

**Option 3: Cloud Integration (Production)**
```
• Export daily
• Upload to S3/Google Cloud
• Set retention policy (30+ days)
• Verify weekly
```

---

## Disaster Recovery

### Scenario: Database corrupted or needs rollback

1. **Identify issue** - Check audit logs
2. **Access import page** - `/admin/import_db`
3. **Select previous backup** - Use timestamped export
4. **Click import** - System creates pre-import backup
5. **Verify restored data** - Check dashboard stats
6. **Review audit logs** - Confirm import timestamp

### Backup Files Location

On server:
```
license_server/
├── data/
│   ├── license_server.db (current database)
│   └── (auto-backups if created by import)
```

---

## File Formats

### SQLite Format (.db)
```
✓ Binary SQLite database
✓ All tables preserved exactly
✓ Indexes and schemas maintained
✓ Full data fidelity
✓ Fast to import
✓ Smallest file size
```

### JSON Format (.json)
```
✓ Human-readable text
✓ Easy to inspect
✓ Good for archival
✓ Larger file size
✓ Easy to share
✓ Can be analyzed with tools
```

**Example JSON structure:**
```json
{
  "export_timestamp": "2024-07-14T14:30:22.123456",
  "server_version": "1.0",
  "tables": {
    "admin_users": [
      {
        "id": 1,
        "username": "admin",
        "password_hash": "...",
        "created_at": "2024-01-01T10:00:00"
      }
    ],
    "license_keys": [
      {
        "id": 1,
        "license_key": "XXXX-XXXX-XXXX-XXXX",
        "customer_name": "John Doe",
        ...
      }
    ]
  }
}
```

---

## Security Considerations

### Best Practices

1. **Store backups securely**
   - ✓ Use encrypted storage
   - ✓ Limit access to authorized users only
   - ✓ Keep offline copy

2. **Verify backups**
   - ✓ Test import on staging first
   - ✓ Verify file integrity
   - ✓ Check file timestamps

3. **Automate backups**
   - ✓ Daily exports
   - ✓ Automated upload to cloud
   - ✓ Retain 30-90 days

4. **Access control**
   - ✓ Only admins can export/import
   - ✓ All actions are audit logged
   - ✓ Review audit logs regularly

5. **Encryption**
   - ✓ Uploaded backups use HTTPS
   - ✓ Consider encrypting stored backups
   - ✓ Use secure passwords for archives

---

## API Reference

### Export Database
```
GET /admin/export_db

Authorization: Session (admin login required)
Response: Binary SQLite file download
Audit: Logged as "database_exported"
```

### Export JSON
```
GET /admin/export_db_json

Authorization: Session (admin login required)
Response: JSON file download
Audit: Logged as "database_exported_json"
```

### Import Database
```
POST /admin/import_db

Authorization: Session (admin login required)
Content-Type: multipart/form-data
Body: {
  backup_file: File (SQLite format)
}

Response: 
- Success: Redirect to dashboard with message
- Error: Display error message

Pre-import backup: Automatically saved as 
  license_server_backup_pre_import_TIMESTAMP.db

Audit: Logged as "database_imported" with backup filename
```

---

## Troubleshooting

### Import fails: "Invalid database file"
```
✗ Cause: File is not SQLite format
✓ Solution: 
  - Verify file is from export (not from elsewhere)
  - Check file header starts with "SQLite format 3"
  - Try a different backup
```

### Import fails: "Permission denied"
```
✗ Cause: Not admin user
✓ Solution:
  - Login as admin user
  - Check session is active
  - Try logging in again
```

### Exported file is corrupted
```
✗ Cause: Download interrupted
✓ Solution:
  - Try exporting again
  - Use different browser/connection
  - Check disk space
```

### Can't find pre-import backup
```
✗ Cause: May have been overwritten
✓ Solution:
  - Check /admin/audit logs
  - Look in data/ directory
  - Files named: license_server_backup_pre_import_*.db
```

---

## Migration & Data Transfer

### To migrate database to another server:

1. **Export from source:**
   ```
   Source Server → Export (.db) → Download backup
   ```

2. **Transfer file:**
   ```
   Download → Secure transfer → Upload to new server
   ```

3. **Import to destination:**
   ```
   Destination Server → Import → Select backup file → Confirm
   ```

4. **Verify:**
   ```
   Check dashboard stats
   Review recent licenses
   Test API endpoints
   ```

---

## Monitoring & Maintenance

### Check audit logs for:
```
✓ database_exported - Regular backups
✓ database_export_json - JSON exports
✓ database_imported - Successful imports
✓ database_import_failed - Failed imports
✓ database_export_failed - Export failures
```

### Regular maintenance:
```
□ Weekly: Export and verify backup
□ Monthly: Test import on staging
□ Quarterly: Review backup retention
□ Annually: Verify disaster recovery plan
```

---

## Contact & Support

For issues or questions:
- Check audit logs: `/admin/audit`
- Review deployment guide: `RAILWAY_DEPLOYMENT.md`
- Contact: Muhammad Saim (Developer)

---

**Last Updated:** 2024-07-14
**Version:** 1.0
