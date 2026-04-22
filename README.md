# ContriKar — Group Expense Splitting

A mobile-first Django web app for splitting group expenses. Uses Supabase PostgreSQL as the database.

---

## Prerequisites

- **Python 3.10+** installed
- **Supabase project** with the following tables already created

### Supabase Table Schema

These tables must exist in your Supabase project before running the app. Django will **not** create them (`managed = False`).

```sql
-- Users
CREATE TABLE users (
    employee_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Expenses
CREATE TABLE expenses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    description TEXT NOT NULL,
    amount NUMERIC NOT NULL,
    paid_by TEXT REFERENCES users(employee_id),
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Expense Splits
CREATE TABLE expense_splits (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    expense_id UUID REFERENCES expenses(id),
    employee_id TEXT REFERENCES users(employee_id),
    share_amount NUMERIC NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);
```

---

## Setup

### 1. Clone & install dependencies

```bash
cd ContriKar
pip install -r requirements.txt
```

### 2. Create your `.env` file

Copy the example and fill in your Supabase credentials:

```bash
copy .env.example .env
```

Edit `.env`:

```env
SECRET_KEY=some-random-secret-key-here
DATABASE_URL=postgresql://postgres.[PROJECT-REF]:[PASSWORD]@aws-0-ap-south-1.pooler.supabase.com:5432/postgres
```

> **Where to find your DATABASE_URL:**
> Supabase Dashboard → Project Settings → Database → Connection string → URI

### 3. Run the server

```bash
python manage.py runserver
```

That's it — no migrations needed. Tables are managed by Supabase.

Visit: **http://127.0.0.1:8000/**

---

## Features

| Feature | Description |
|---------|-------------|
| **Login** | Enter employee ID — if registered, you're in; if not, redirected to register |
| **Register** | One-time setup: employee ID + full name |
| **Dashboard** | Balance cards + expense log (newest first, IST timestamps) |
| **Add Expense** | Description, amount, payer, multi-select equal split |
| **Settle Up** | Greedy algorithm shows minimum transactions to clear all debts |

---

## Tech Stack

- Django 4.2 (views + templates, no DRF)
- Supabase PostgreSQL (via `DATABASE_URL`)
- `psycopg2-binary` for DB connection
- `python-decouple` + `dj-database-url` for config
- Vanilla CSS (dark theme, mobile-first)
- No JavaScript frameworks
