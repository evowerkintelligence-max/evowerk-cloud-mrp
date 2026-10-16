FROM python:3.14-slim

WORKDIR /app

# unixodbc-dev + gcc: needed to build the pyodbc C extension (SQL Server
# connector). This gets pyodbc installable, but actually reaching a SQL Server
# instance also needs Microsoft's ODBC Driver 17/18 registered in this image -
# not added here (proprietary package, separate apt repo + EULA) - the SQL
# Server connector's test() call raises a clear error until that's added.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc unixodbc-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.lock.txt .
RUN pip install --no-cache-dir -r requirements.lock.txt

COPY . .

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
