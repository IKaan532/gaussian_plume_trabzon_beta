# Gaussian Plume — Local DevOps Kılavuzu

Bu belge, Gaussian Plume uygulamasının yerel Kubernetes ortamında nasıl deploy edildiğini,
CI/CD pipeline'ının nasıl kurulduğunu ve monitoring/logging stack'inin nasıl yapılandırıldığını
adım adım açıklamaktadır.

---

## Değişkenler

Aşağıdaki komutlarda bu değerleri kendi ortamınıza göre değiştirin:

| Değişken | Örnek Değer | Açıklama |
|---|---|---|
| `APP_NAME` | `gaussian-plume` | Uygulama adı |
| `APP_NAMESPACE` | `gaussian-plume` | Kubernetes namespace |
| `REGISTRY` | `localhost:5000` | Yerel Docker registry adresi |
| `GITEA_URL` | `http://localhost:30880` | Gitea web arayüzü |
| `GITEA_USER` | `admin` | Gitea kullanıcı adı |
| `GITEA_REPO` | `gaussian-plume` | Gitea repo adı |
| `APP_PORT` | `30501` | Uygulama NodePort |
| `GRAFANA_PORT` | `30300` | Grafana NodePort |
| `PROMETHEUS_PORT` | `30090` | Prometheus NodePort |
| `GITEA_PORT` | `30880` | Gitea NodePort |
| `OWM_API_KEY` | `<your_key>` | OpenWeatherMap API anahtarı |

---

## Gereksinimler

- Docker Desktop (Kubernetes etkin)
- Git
- Helm v3+
- kubectl

Kubernetes durumunu doğrula:

```bash
kubectl get nodes
# NAME             STATUS   ROLES           AGE
# docker-desktop   Ready    control-plane   ...
```

Helm versiyonunu kontrol et:

```bash
helm version
```

---

## 1. Yerel Docker Registry Kurulumu

```bash
docker run -d \
  --name local-registry \
  --restart=always \
  -p 5000:5000 \
  registry:2
```

Registry çalışıyor mu kontrol et:

```bash
curl http://${REGISTRY}/v2/
# {}
```

Docker Desktop → Settings → Docker Engine'e insecure registry ekle:

```json
{
  "insecure-registries": ["localhost:5000"]
}
```

---

## 2. Docker Image Build & Push

```bash
cd /path/to/${APP_NAME}

docker build -t ${REGISTRY}/${APP_NAME}:latest .

docker push ${REGISTRY}/${APP_NAME}:latest
```

---

## 3. Kubernetes Namespace & Secret

```bash
kubectl create namespace ${APP_NAMESPACE}

kubectl create secret generic owm-api-key \
  --from-literal=OWM_API_KEY=${OWM_API_KEY} \
  --namespace ${APP_NAMESPACE}
```

---

## 4. Kubernetes Deployment

`k8s/deployment.yaml` dosyasını uygula:

```bash
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml
```

Pod durumunu izle:

```bash
kubectl get pods -n ${APP_NAMESPACE} -w
```

Uygulamaya eriş:

```
http://localhost:${APP_PORT}
```

### deployment.yaml özeti

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ${APP_NAME}
  namespace: ${APP_NAMESPACE}
spec:
  replicas: 2
  selector:
    matchLabels:
      app: ${APP_NAME}
  template:
    spec:
      affinity:
        podAntiAffinity:
          preferredDuringSchedulingIgnoredDuringExecution:
            - weight: 100
              podAffinityTerm:
                labelSelector:
                  matchExpressions:
                    - key: app
                      operator: In
                      values: [${APP_NAME}]
                topologyKey: kubernetes.io/hostname
      containers:
        - name: ${APP_NAME}
          image: ${REGISTRY}/${APP_NAME}:latest
          ports:
            - containerPort: 8501
          env:
            - name: OWM_API_KEY
              valueFrom:
                secretKeyRef:
                  name: owm-api-key
                  key: OWM_API_KEY
```

> **podAntiAffinity:** Aynı pod'un aynı node'a iki kez schedule edilmesini önler.
> `preferred` kullanıldı çünkü ortamda tek node var — `required` olsaydı pod Pending kalırdı.

---

## 5. Gitea — Yerel Git Sunucusu

### Kurulum (Helm OCI)

```bash
kubectl create namespace gitea

helm install gitea oci://registry-1.docker.io/giteacharts/gitea \
  --namespace gitea \
  --set service.http.type=NodePort \
  --set service.http.nodePort=${GITEA_PORT} \
  --set gitea.admin.username=${GITEA_USER} \
  --set gitea.admin.password=Admin1234! \
  --set gitea.admin.email=admin@local.com \
  --set postgresql-ha.enabled=false \
  --set redis-cluster.enabled=false \
  --set gitea.config.database.DB_TYPE=sqlite3 \
  --set gitea.config.session.PROVIDER=memory \
  --set gitea.config.cache.ADAPTER=memory \
  --set gitea.config.queue.TYPE=level
```

> **Not:** `helm repo add` yerine OCI registry kullanıldı çünkü `dl.gitea.com`
> IPv6 üzerinden erişilemiyor olabilir.

### Repo Oluştur & Kodu Push Et

```bash
cd /path/to/${APP_NAME}

git init
git add .
git commit -m "initial commit"
git remote add origin http://localhost:${GITEA_PORT}/${GITEA_USER}/${GITEA_REPO}.git
git push -u origin master
```

---

## 6. Gitea Actions Runner

### RBAC — Runner'a Deploy Yetkisi Ver

```bash
kubectl apply -f - <<EOF
apiVersion: v1
kind: ServiceAccount
metadata:
  name: runner-sa
  namespace: gitea-runner
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: deployer
  namespace: ${APP_NAMESPACE}
rules:
  - apiGroups: ["apps"]
    resources: ["deployments"]
    verbs: ["get", "patch", "list"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: runner-deployer
  namespace: ${APP_NAMESPACE}
subjects:
  - kind: ServiceAccount
    name: runner-sa
    namespace: gitea-runner
roleRef:
  kind: Role
  name: deployer
  apiGroup: rbac.authorization.k8s.io
EOF
```

### Runner Deploy

Gitea → Repo → Settings → Actions → Runners → **Create new Runner** → token kopyala.

```bash
kubectl create namespace gitea-runner

kubectl create secret generic gitea-runner-token \
  --from-literal=token=<RUNNER_TOKEN> \
  --namespace gitea-runner

kubectl apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: gitea-runner
  namespace: gitea-runner
spec:
  replicas: 1
  selector:
    matchLabels:
      app: gitea-runner
  template:
    metadata:
      labels:
        app: gitea-runner
    spec:
      serviceAccountName: runner-sa
      containers:
        - name: runner
          image: gitea/act_runner:latest
          env:
            - name: GITEA_INSTANCE_URL
              value: "http://gitea-http.gitea.svc.cluster.local:3000"
            - name: GITEA_RUNNER_REGISTRATION_TOKEN
              valueFrom:
                secretKeyRef:
                  name: gitea-runner-token
                  key: token
            - name: GITEA_RUNNER_NAME
              value: "k8s-runner"
          volumeMounts:
            - name: docker-sock
              mountPath: /var/run/docker.sock
      volumes:
        - name: docker-sock
          hostPath:
            path: /var/run/docker.sock
EOF
```

> **Docker socket mount:** Runner, host'un Docker daemon'ına erişerek image build & push yapabilir.
> Job container'ları Kubernetes pod'u olarak değil, host Docker container'ı olarak çalışır.
> Bu nedenle job içinden `kubernetes.svc.cluster.local` DNS'i çözülemez —
> bunun yerine `host.docker.internal` kullanılır.

---

## 7. CI/CD Pipeline (.gitea/workflows/ci.yml)

```yaml
name: CI/CD — Build · Trivy · Push · Deploy

on:
  push:
    branches: [master]

jobs:
  ci:
    runs-on: ubuntu-latest
    steps:
      - name: Kodu çek
        run: |
          git clone http://${GITEA_USER}:Admin1234!@host.docker.internal:${GITEA_PORT}/${GITEA_USER}/${GITEA_REPO}.git .
          git checkout ${{ gitea.sha }}

      - name: Docker image build
        run: |
          docker build -t ${REGISTRY}/${APP_NAME}:${{ gitea.sha }} .
          docker tag ${REGISTRY}/${APP_NAME}:${{ gitea.sha }} ${REGISTRY}/${APP_NAME}:latest

      - name: Trivy güvenlik taraması
        run: |
          docker run --rm \
            -v /var/run/docker.sock:/var/run/docker.sock \
            aquasec/trivy:latest image \
            --exit-code 0 \
            --severity HIGH,CRITICAL \
            --format table \
            ${REGISTRY}/${APP_NAME}:${{ gitea.sha }}

      - name: Registry push
        run: |
          docker push ${REGISTRY}/${APP_NAME}:${{ gitea.sha }}
          docker push ${REGISTRY}/${APP_NAME}:latest

      - name: Kubernetes deploy
        env:
          KUBE_CONFIG: ${{ secrets.KUBE_CONFIG }}
        run: |
          curl -sLO "https://dl.k8s.io/release/v1.28.0/bin/linux/amd64/kubectl"
          chmod +x kubectl
          mkdir -p ~/.kube
          echo "$KUBE_CONFIG" | base64 -d > ~/.kube/config
          sed -i 's/kubernetes.docker.internal/host.docker.internal/g' ~/.kube/config
          ./kubectl rollout restart deployment/${APP_NAME} \
            --namespace=${APP_NAMESPACE} \
            --insecure-skip-tls-verify
```

### KUBE_CONFIG Secret Oluştur

```bash
kubectl config view --raw --minify | base64 -w 0
```

Çıktıyı Gitea → Repo → Settings → Actions → **Secrets** → `KUBE_CONFIG` olarak ekle.

### Pipeline Akışı

```
git push
    │
    ▼
Gitea Actions tetiklenir
    │
    ├─► Kodu çek (host.docker.internal üzerinden)
    ├─► Docker image build
    ├─► Trivy HIGH/CRITICAL tarama (exit-code 0 → hata olsa bile devam)
    ├─► localhost:5000 registry'ye push
    └─► kubectl rollout restart → Kubernetes yeni image'ı çeker
```

> **Trivy exit-code 0:** Pipeline güvenlik açıklarında durmuyor, sadece rapor üretiyor.
> Üretim ortamında `--exit-code 1` yaparak CRITICAL bulgu varsa pipeline'ı durdurabilirsiniz.

---

## 8. Prometheus + Grafana Monitoring

```bash
kubectl create namespace monitoring

helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

helm install monitoring prometheus-community/kube-prometheus-stack \
  --namespace monitoring \
  --set grafana.service.type=NodePort \
  --set grafana.service.nodePort=${GRAFANA_PORT} \
  --set prometheus.service.type=NodePort \
  --set prometheus.service.nodePort=${PROMETHEUS_PORT} \
  --set alertmanager.enabled=false \
  --set prometheus.prometheusSpec.scrapeInterval=30s
```

Grafana şifresini al:

```bash
kubectl --namespace monitoring get secrets monitoring-grafana \
  -o jsonpath="{.data.admin-password}" | base64 -d
```

Grafana'ya giriş: `http://localhost:${GRAFANA_PORT}` — kullanıcı: `admin`

### Prometheus Data Source

Grafana → Connections → Data Sources → Add → Prometheus

```
URL: http://monitoring-kube-prometheus-prometheus.monitoring.svc.cluster.local:9090
```

### Kubernetes Dashboard Import

Grafana → Dashboards → Import → ID: `15661` → Prometheus seç → Import

---

## 9. Loki — Log Toplama

```bash
helm repo add grafana https://grafana.github.io/helm-charts
helm repo update

helm install loki grafana/loki-stack \
  --namespace monitoring \
  --set grafana.enabled=false \
  --set prometheus.enabled=false \
  --set loki.persistence.enabled=false \
  --set promtail.enabled=true
```

### Loki Data Source

Grafana → Connections → Data Sources → Add → Loki

```
URL: http://loki:3100
```

> **Not:** "Save & Test" başarısız görünebilir (Grafana 11 / loki-stack uyumsuzluğu)
> ancak gerçekte bağlantı çalışır. Explore → Label browser → namespace=gaussian-plume
> ile logları sorgulayabilirsiniz.

### Log Sorgulama (LogQL)

```logql
{namespace="gaussian-plume"}

{namespace="gaussian-plume", container="gaussian-plume"} |= "ERROR"

{namespace="gitea"} | json | level="error"
```

---

## 10. Tüm Servisler

```bash
kubectl get pods --all-namespaces
```

| Servis | URL | Namespace |
|---|---|---|
| Gaussian Plume App | `http://localhost:30501` | `gaussian-plume` |
| Gitea | `http://localhost:30880` | `gitea` |
| Grafana | `http://localhost:30300` | `monitoring` |
| Prometheus | `http://localhost:30090` | `monitoring` |
| Loki | `cluster-internal:3100` | `monitoring` |
| Docker Registry | `http://localhost:5000` | — |

---

## Sık Kullanılan Komutlar

```bash
kubectl get pods -n gaussian-plume
kubectl get pods -n gitea
kubectl get pods -n monitoring
kubectl get pods -n gitea-runner

kubectl logs -n gaussian-plume -l app=gaussian-plume --tail=50

kubectl rollout restart deployment/gaussian-plume -n gaussian-plume

kubectl rollout status deployment/gaussian-plume -n gaussian-plume

kubectl describe pod -n gaussian-plume <pod-name>
```

---

## Mimari Özeti

```
┌─────────────────────────────────────────────────────────┐
│                  Docker Desktop                          │
│                                                          │
│  ┌──────────────┐    ┌──────────────┐                   │
│  │   Gitea       │    │  Local       │                   │
│  │  :30880       │    │  Registry    │                   │
│  │  (Git + CI)   │    │  :5000       │                   │
│  └──────┬───────┘    └──────▲───────┘                   │
│         │ push trigger       │ push image                │
│         ▼                    │                           │
│  ┌──────────────┐            │                           │
│  │ Actions      │────────────┘                           │
│  │ Runner       │                                        │
│  │ (Trivy scan) │──────────────────────────────┐         │
│  └─────────────┘  kubectl rollout restart      │         │
│                                                ▼         │
│  ┌──────────────────────────────────────────────────┐   │
│  │           Kubernetes Cluster                      │   │
│  │                                                   │   │
│  │  gaussian-plume ns:  [pod1] [pod2]  :30501        │   │
│  │  gitea ns:           [gitea]        :30880        │   │
│  │  monitoring ns:      [prometheus]   :30090        │   │
│  │                      [grafana]      :30300        │   │
│  │                      [loki]                       │   │
│  │                      [promtail] ──► loki          │   │
│  └──────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```
