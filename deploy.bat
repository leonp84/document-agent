@echo off
setlocal

set PROJECT=docassist-494914
set REGION=europe-west3
set REPO=docassist
set SERVICE=docassist
set IMAGE=%REGION%-docker.pkg.dev/%PROJECT%/%REPO%/%SERVICE%:latest
set HEALTH_URL=https://docassist-86540080152.%REGION%.run.app/health

echo.
echo === [1/2] Building image: %IMAGE%
echo.
call gcloud builds submit --tag %IMAGE% --project %PROJECT%
if errorlevel 1 (
    echo.
    echo Build failed. Aborting deploy.
    exit /b 1
)

echo.
echo === [2/2] Deploying to Cloud Run service: %SERVICE% (%REGION%)
echo.
call gcloud run deploy %SERVICE% --image %IMAGE% --region %REGION% --project %PROJECT%
if errorlevel 1 (
    echo.
    echo Deploy failed.
    exit /b 1
)

echo.
echo === Verifying %HEALTH_URL%
curl -s -w "HTTP %%{http_code}\n" %HEALTH_URL%

endlocal
