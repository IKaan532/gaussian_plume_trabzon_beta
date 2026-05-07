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
# Beklenen çıktı:
# NAME             STATUS   ROLES           AGE
# docker-desktop   Ready    control-plane   ...
# "Ready" görünmüyorsa Docker Desktop > Settings > Kubernetes > Enable Kubernetes
```

Helm versiyonunu kontrol et:

```bash
helm version
# v3.x.x veya üzeri olmalı
```

---

## 1. Yerel Docker Registry Kurulumu

```bash
docker run -d \              # arka planda (detach) çalıştır
  --name local-registry \   # container'a isim ver — yönetimi kolaylaştırır
  --restart=always \        # Docker yeniden başlayınca registry de otomatik kalksın
  -p 5000:5000 \            # host:container port yönlendirmesi
  registry:2                # resmi Docker registry image'ı (v2 API)
```

Registry çalışıyor mu kontrol et:

```bash
curl http://${REGISTRY}/v2/
# {} — boş JSON dönüyorsa registry hazır demektir
```

Docker Desktop → Settings → Docker Engine'e insecure registry ekle:

```json
{
  "insecure-registries": ["localhost:5000"]
  // localhost:5000 HTTPS olmadan push/pull yapabilmek için gerekli
  // Üretimde bu ayar kullanılmaz, TLS zorunlu tutulur
}
```

---

## 2. Docker Image Build & Push

```bash
cd /path/to/${APP_NAME}                         # proje dizinine geç

docker build -t ${REGISTRY}/${APP_NAME}:latest . # Dockerfile'dan image oluştur
                                                  # -t ile registry/isim:tag belirlenir
                                                  # . mevcut dizini build context olarak kullan

docker push ${REGISTRY}/${APP_NAME}:latest        # image'ı yerel registry'ye gönder
```

---

## 3. Kubernetes Namespace & Secret

```bash
kubectl create namespace ${APP_NAMESPACE}
# Uygulamayı izole bir namespace'de çalıştır
# Farklı uygulamalar farklı namespace'lerde → daha kolay yönetim ve RBAC

kubectl create secret generic owm-api-key \      # Secret türü: generic (key-value)
  --from-literal=OWM_API_KEY=${OWM_API_KEY} \   # API key'i literal değer olarak ekle
  --namespace ${APP_NAMESPACE}                   # Secret hangi namespace'de olacak
# Secret, deployment.yaml'da env olarak inject edilir
# Böylece API key kod içinde veya image'da yer almaz
```

---

## 4. Kubernetes Deployment

`k8s/deployment.yaml` dosyasını uygula:

```bash
kubectl apply -f k8s/deployment.yaml  # Deployment kaynağını oluştur/güncelle
kubectl apply -f k8s/service.yaml     # NodePort Service'i oluştur — dışarıdan erişim sağlar
```

Pod durumunu izle:

```bash
kubectl get pods -n ${APP_NAMESPACE} -w
# -w (watch): pod durumu değişince otomatik günceller
# Running ve READY 1/1 olunca hazır
```

Uygulamaya eriş:

```
http://localhost:${APP_PORT}
```

### deployment.yaml özeti

```yaml
apiVersion: apps/v1        # Deployment için apps/v1 API grubu kullanılır
kind: Deployment           # Kaynak türü: Deployment (pod yönetimi + rolling update)
metadata:
  name: ${APP_NAME}        # Deployment'ın adı
  namespace: ${APP_NAMESPACE} # Hangi namespace'de oluşturulacak

spec:
  replicas: 2              # 2 pod çalıştır — yüksek erişilebilirlik için
  selector:
    matchLabels:
      app: ${APP_NAME}     # Bu label'a sahip pod'ları yönet

  template:
    spec:
      affinity:
        podAntiAffinity:                              # Pod'ların aynı node'a düşmesini engelle
          preferredDuringSchedulingIgnoredDuringExecution:
          # "preferred" = tercih et ama zorunlu değil
          # "required" olsaydı single node'da pod Pending kalırdı
            - weight: 100                            # Bu kuralın ağırlığı (1-100)
              podAffinityTerm:
                labelSelector:
                  matchExpressions:
                    - key: app
                      operator: In
                      values: [${APP_NAME}]          # Aynı app label'lı pod'ları dağıt
                topologyKey: kubernetes.io/hostname  # Node bazında dağılım kriteri

      containers:
        - name: ${APP_NAME}
          image: ${REGISTRY}/${APP_NAME}:latest      # Yerel registry'den image çek
          ports:
            - containerPort: 8501                    # Streamlit varsayılan portu
          env:
            - name: OWM_API_KEY
              valueFrom:
                secretKeyRef:
                  name: owm-api-key                  # Önceden oluşturulan Secret adı
                  key: OWM_API_KEY                   # Secret içindeki key adı
```

> **podAntiAffinity:** Aynı pod'un aynı node'a iki kez schedule edilmesini önler.
> `preferred` kullanıldı çünkü ortamda tek node var — `required` olsaydı pod Pending kalırdı.

---

## 5. Gitea — Yerel Git Sunucusu

### Kurulum (Helm OCI)

```bash
kubectl create namespace gitea   # Gitea için ayrı namespace

helm install gitea oci://registry-1.docker.io/giteacharts/gitea \
# "oci://" ile Docker Hub'daki OCI artifact registry kullanılır
# Alternatif "helm repo add" IPv6 sorununa takılabilir
  --namespace gitea \
  --set service.http.type=NodePort \          # Dışarıdan erişim için NodePort
  --set service.http.nodePort=${GITEA_PORT} \ # localhost:30880 olarak erişilecek
  --set gitea.admin.username=${GITEA_USER} \  # İlk admin kullanıcısı
  --set gitea.admin.password=Admin1234! \     # Admin şifresi (üretimde değiştir)
  --set gitea.admin.email=admin@local.com \
  --set postgresql-ha.enabled=false \         # Harici DB yok → SQLite kullan (yerel için yeterli)
  --set redis-cluster.enabled=false \         # Cache için Redis yok → memory kullan
  --set gitea.config.database.DB_TYPE=sqlite3 \ # Basit tek dosya veritabanı
  --set gitea.config.session.PROVIDER=memory \  # Session'ları memory'de tut
  --set gitea.config.cache.ADAPTER=memory \     # Cache'i memory'de tut
  --set gitea.config.queue.TYPE=level            # Job queue için LevelDB kullan
```

> **Not:** `helm repo add` yerine OCI registry kullanıldı çünkü `dl.gitea.com`
> IPv6 üzerinden erişilemiyor olabilir.

### Repo Oluştur & Kodu Push Et

```bash
cd /path/to/${APP_NAME}   # Proje dizinine geç

git init                  # Yerel git deposu başlat
git add .                 # Tüm dosyaları staging area'ya ekle
git commit -m "initial commit"  # İlk commit oluştur

git remote add origin http://localhost:${GITEA_PORT}/${GITEA_USER}/${GITEA_REPO}.git
# Remote adı "origin" olarak tanımla → Gitea üzerindeki repo URL'si

git push -u origin master
# -u: upstream set et (sonraki push'larda sadece "git push" yeter)
# master branch'ini push et
```

---

## 6. Gitea Actions Runner

### RBAC — Runner'a Deploy Yetkisi Ver

`k8s/rbac.yaml` dosyası:

```yaml
apiVersion: v1
kind: ServiceAccount       # Runner pod'unun Kubernetes API ile konuşacağı kimlik
metadata:
  name: runner-sa
  namespace: gitea-runner
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role                 # Belirli bir namespace'de geçerli yetki tanımı
metadata:
  name: deployer
  namespace: gaussian-plume  # Yetki sadece bu namespace'de geçerli
rules:
  - apiGroups: ["apps"]        # Deployment kaynağı "apps" API grubunda
    resources: ["deployments"] # Sadece Deployment'lara erişim ver
    verbs: ["get", "patch", "list"]
    # get: mevcut durumu oku
    # patch: rollout restart için güncelle
    # list: tüm deployment'ları listele
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding           # ServiceAccount ile Role'ü birbirine bağla
metadata:
  name: runner-deployer
  namespace: gaussian-plume
subjects:
  - kind: ServiceAccount
    name: runner-sa
    namespace: gitea-runner  # runner-sa hangi namespace'de tanımlı
roleRef:
  kind: Role
  name: deployer
  apiGroup: rbac.authorization.k8s.io
```

```bash
kubectl apply -f k8s/rbac.yaml   # ServiceAccount + Role + RoleBinding tek komutla uygula
```

### Runner Deploy

Gitea → Repo → Settings → Actions → Runners → **Create new Runner** → token kopyala.

```bash
kubectl create namespace gitea-runner  # Runner için ayrı namespace

kubectl create secret generic gitea-runner-token \
  --from-literal=token=<RUNNER_TOKEN> \ # Gitea'dan alınan registration token
  --namespace gitea-runner
```

`k8s/gitea-runner.yaml` dosyası:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: gitea-runner
  namespace: gitea-runner
spec:
  replicas: 1              # Tek runner yeterli (paralel job için artırılabilir)
  selector:
    matchLabels:
      app: gitea-runner
  template:
    metadata:
      labels:
        app: gitea-runner
    spec:
      serviceAccountName: runner-sa   # RBAC ile tanımladığımız ServiceAccount
      containers:
        - name: runner
          image: gitea/act_runner:latest   # Gitea'nın resmi Actions runner image'ı
          env:
            - name: GITEA_INSTANCE_URL
              value: "http://gitea-http.gitea.svc.cluster.local:3000"
              # Runner pod'u Kubernetes içinden Gitea'ya bu adresle bağlanır
              # cluster.local = Kubernetes iç DNS domain'i
            - name: GITEA_RUNNER_REGISTRATION_TOKEN
              valueFrom:
                secretKeyRef:
                  name: gitea-runner-token  # Önceden oluşturulan Secret
                  key: token
            - name: GITEA_RUNNER_NAME
              value: "k8s-runner"           # Gitea arayüzünde görünecek runner adı
          volumeMounts:
            - name: docker-sock
              mountPath: /var/run/docker.sock  # Docker socket'i container içine bağla
      volumes:
        - name: docker-sock
          hostPath:
            path: /var/run/docker.sock
            # Host'un Docker socket'ini kullan
            # Böylece runner, host'un Docker daemon'ını yönetebilir
            # image build/push için gerekli
```

```bash
kubectl apply -f k8s/gitea-runner.yaml   # Runner Deployment'ını uygula
```

> **Docker socket mount:** Runner, host'un Docker daemon'ına erişerek image build & push yapabilir.
> Job container'ları Kubernetes pod'u olarak değil, host Docker container'ı olarak çalışır.
> Bu nedenle job içinden `kubernetes.svc.cluster.local` DNS'i çözülemez —
> bunun yerine `host.docker.internal` kullanılır.

---

## 7. CI/CD Pipeline — Gerçek Dosya İçeriği (.gitea/workflows/ci.yml)

```yaml
name: CI/CD — Build · Trivy · Push · Deploy   # Gitea Actions arayüzünde görünen pipeline adı

on:
  push:                    # Tetikleyici: push eventi
    branches: [master]     # Sadece master branch'e push olunca çalış

jobs:
  ci:
    runs-on: ubuntu-latest   # Runner'ın sağladığı label — act_runner ubuntu-latest destekler

    steps:

      - name: Kodu çek
        run: |
          git clone http://admin:Admin1234!@host.docker.internal:30880/admin/gaussian-plume.git .
          # host.docker.internal → Docker container'ından host makineye erişim adresi
          # localhost:30880 değil çünkü job container'ı host'un ağını göremez
          # Kimlik bilgileri URL'e gömülü → Basic Auth ile clone
          git checkout ${{ gitea.sha }}
          # Push edilen tam commit hash'ine geç → doğru kodu build et

      - name: Docker image build
        run: |
          docker build -t localhost:5000/gaussian-plume:${{ gitea.sha }} .
          # Her commit için benzersiz tag → hangi commit'in hangi image olduğu izlenebilir
          # localhost:5000 → host Docker daemon üzerinden yerel registry'ye erişim
          docker tag localhost:5000/gaussian-plume:${{ gitea.sha }} localhost:5000/gaussian-plume:latest
          # latest tag → Kubernetes'in her zaman en yeni image'ı çekmesi için

      - name: Trivy — Zafiyet & Secret & Konfigürasyon Taraması
        run: |
          docker run --rm \
          # --rm: tarama bitince Trivy container'ını sil
            -v /var/run/docker.sock:/var/run/docker.sock \
            # Docker socket bağla → Trivy image layer'larına doğrudan erişebilsin
            -v /tmp/trivy-cache:/root/.cache/trivy \
            # CVE veritabanını cache'le → her çalıştırmada internetten indirme
            aquasec/trivy:latest image \
            # "image" subcommand → container image tara
            --exit-code 0 \
            # 0 → zafiyet bulunsa bile pipeline devam eder (sadece raporla)
            # Üretimde --exit-code 1 yapılırsa CRITICAL bulgu pipeline'ı durdurur
            --scanners vuln,secret,misconfig \
            # vuln     → OS ve dil framework CVE'leri (pip, apt vb.)
            # secret   → API key, token, şifre gibi gizli bilgi sızıntısı
            # misconfig → Dockerfile'da root user, latest tag, --privileged gibi hatalar
            --severity MEDIUM,HIGH,CRITICAL \
            # LOW ve INFORMATIONAL atla → aksiyon gerektiren bulgulara odaklan
            --format table \
            # Okunabilir tablo formatı → pipeline logunda görünür
            localhost:5000/gaussian-plume:${{ gitea.sha }}
            # Taranacak image: az önce build edilen commit-specific tag

      - name: Registry push
        run: |
          docker push localhost:5000/gaussian-plume:${{ gitea.sha }}
          # Commit hash'li tag'i registry'ye gönder → versiyon geçmişi tutulur
          docker push localhost:5000/gaussian-plume:latest
          # latest tag'i de güncelle → Kubernetes bu tag'i kullanıyor

      - name: Kubernetes deploy
        env:
          KUBE_CONFIG: ${{ secrets.KUBE_CONFIG }}
          # Gitea Secrets'tan kubeconfig çek → pipeline içinde kubectl kullanabilmek için
        run: |
          curl -sLO "https://dl.k8s.io/release/v1.28.0/bin/linux/amd64/kubectl"
          # kubectl binary'sini indir (job container'ında yüklü değil)
          # -s: sessiz mod, -L: redirect takip et, -O: dosya adını koru
          chmod +x kubectl
          # İndirilen binary'yi çalıştırılabilir yap
          mkdir -p ~/.kube
          # kubeconfig dizinini oluştur (yoksa hata verir)
          echo "$KUBE_CONFIG" | base64 -d > ~/.kube/config
          # Secret'taki base64 kubeconfig'i decode edip standart konuma yaz
          sed -i 's/kubernetes.docker.internal/host.docker.internal/g' ~/.kube/config
          # kubeconfig'deki API server adresi "kubernetes.docker.internal" olarak gelir
          # Job container'ı bu adresi çözemez → host.docker.internal ile değiştir
          ./kubectl rollout restart deployment/gaussian-plume \
          # Deployment'ı yeniden başlat → pod'lar yeni latest image'ı çeker
            --namespace=gaussian-plume \
            --insecure-skip-tls-verify
            # Yerel cluster'da self-signed sertifika olduğundan TLS doğrulamasını atla
```

### KUBE_CONFIG Secret Oluştur

```bash
kubectl config view --raw --minify | base64 -w 0
# --raw: sertifikaları gömülü göster (referans değil)
# --minify: sadece aktif context'i göster (gereksiz cluster bilgileri gelmesin)
# base64 -w 0: tek satır base64 çıktısı (-w 0 = satır kaydırma yok)
```

Çıktıyı Gitea → Repo → Settings → Actions → **Secrets** → `KUBE_CONFIG` olarak ekle.

### Pipeline Akışı

```
git push
    │
    ▼
Gitea Actions tetiklenir
    │
    ├─► Kodu çek          (host.docker.internal:30880 üzerinden Basic Auth ile clone)
    ├─► Docker image build (commit hash tag + latest tag)
    ├─► Trivy tarama       (vuln + secret + misconfig | MEDIUM/HIGH/CRITICAL | exit-code 0)
    ├─► Registry push      (localhost:5000 → her iki tag de push edilir)
    └─► kubectl rollout restart → Kubernetes yeni latest image'ı çeker
```

> **Trivy exit-code 0:** Pipeline güvenlik açıklarında durmuyor, sadece rapor üretiyor.
> Üretim ortamında `--exit-code 1` yaparak CRITICAL bulgu varsa pipeline'ı durdurabilirsiniz.
>
> **Tarama kapsamı:**
> - `vuln` → OS paketleri + Python/pip framework CVE'leri
> - `secret` → Kod içindeki API key, token, şifre tespiti
> - `misconfig` → Dockerfile'da root user, latest tag, `--privileged` gibi yanlış konfigürasyonlar
> - Severity: `MEDIUM` ve üzeri raporlanır

---

## 8. Prometheus + Grafana Monitoring

```bash
kubectl create namespace monitoring   # Monitoring bileşenleri için ayrı namespace

helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
# Prometheus Helm chart deposunu ekle
helm repo update
# Tüm repo'ları güncelle → en güncel chart versiyonları gelsin

helm install monitoring prometheus-community/kube-prometheus-stack \
# kube-prometheus-stack = Prometheus + Grafana + kube-state-metrics + node-exporter
# Tek Helm chart ile tüm monitoring stack kurulur
  --namespace monitoring \
  --set grafana.service.type=NodePort \           # Grafana dışarıdan erişilebilir olsun
  --set grafana.service.nodePort=${GRAFANA_PORT} \ # localhost:30300
  --set prometheus.service.type=NodePort \         # Prometheus dışarıdan erişilebilir olsun
  --set prometheus.service.nodePort=${PROMETHEUS_PORT} \ # localhost:30090
  --set alertmanager.enabled=false \              # Yerel ortamda alert yönetimine gerek yok
  --set prometheus.prometheusSpec.scrapeInterval=30s
  # Her 30 saniyede bir metrik topla (varsayılan 60s)
```

Grafana şifresini al:

```bash
kubectl --namespace monitoring get secrets monitoring-grafana \
  -o jsonpath="{.data.admin-password}" | base64 -d
# Secret içindeki admin-password alanını çek ve base64 decode et
```

Grafana'ya giriş: `http://localhost:${GRAFANA_PORT}` — kullanıcı: `admin`

### Prometheus Data Source

Grafana → Connections → Data Sources → Add → Prometheus

```
URL: http://monitoring-kube-prometheus-prometheus.monitoring.svc.cluster.local:9090
# Kubernetes iç DNS formatı: <servis-adı>.<namespace>.svc.cluster.local:<port>
# Grafana pod'u ile Prometheus pod'u aynı cluster'da → iç ağ üzerinden iletişim
```

### Kubernetes Dashboard Import

```
Grafana → Dashboards → Import → ID: 15661 → Prometheus data source seç → Import
# 15661: Grafana.com'dan hazır K8s dashboard
# Node CPU/RAM, Pod sayısı, Namespace bazlı kaynak kullanımı gösterir
```

---

## 9. Loki — Log Toplama

```bash
helm repo add grafana https://grafana.github.io/helm-charts
# Grafana Labs'ın Helm repo'sunu ekle (Loki burada)
helm repo update

helm install loki grafana/loki-stack \
# loki-stack = Loki (log saklama) + Promtail (log toplama agent)
  --namespace monitoring \      # Prometheus/Grafana ile aynı namespace
  --set grafana.enabled=false \ # Grafana zaten kurulu, tekrar kurma
  --set prometheus.enabled=false \ # Prometheus zaten kurulu, tekrar kurma
  --set loki.persistence.enabled=false \ # Disk'e yazma yok → pod silinince loglar gider
                                          # Üretimde true yapılır ve PVC bağlanır
  --set promtail.enabled=true
  # Promtail: her node'da çalışır, pod log dosyalarını okur, Loki'ye iletir
  # DaemonSet olarak deploy edilir → tüm node'lardaki loglar toplanır
```

### Loki Data Source

Grafana → Connections → Data Sources → Add → Loki

```
URL: http://loki:3100
# Loki servisi "loki" adıyla monitoring namespace'inde çalışıyor
# Grafana da aynı namespace'de → kısa servis adı yeterli (FQDN gerekmez)
```

> **Not:** "Save & Test" başarısız görünebilir (Grafana 11 / loki-stack uyumsuzluğu)
> ancak gerçekte bağlantı çalışır. Explore → Label browser → namespace=gaussian-plume
> ile logları sorgulayabilirsiniz.

### Log Sorgulama (LogQL)

```logql
{namespace="gaussian-plume"}
# gaussian-plume namespace'indeki tüm pod logları

{namespace="gaussian-plume", container="gaussian-plume"} |= "ERROR"
# Sadece ERROR içeren satırları filtrele
# |= "metin" → satır içinde metin ara

{namespace="gitea"} | json | level="error"
# JSON formatlı logları parse et → level alanı "error" olanları getir
```

---

## 10. Tüm Servisler

```bash
kubectl get pods --all-namespaces
# Tüm namespace'lerdeki pod'ları listele — genel sistem durumunu görmek için
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
kubectl get pods -n gaussian-plume      # Uygulama pod'larının durumunu gör
kubectl get pods -n gitea               # Gitea pod'larının durumunu gör
kubectl get pods -n monitoring          # Prometheus/Grafana/Loki pod durumları
kubectl get pods -n gitea-runner        # CI/CD runner pod durumu

kubectl logs -n gaussian-plume -l app=gaussian-plume --tail=50
# -l: label selector ile pod filtrele
# --tail=50: son 50 satırı göster

kubectl rollout restart deployment/gaussian-plume -n gaussian-plume
# Deployment'ı yeniden başlat → yeni image varsa çeker
# Sıfır kesinti ile rolling update yapar

kubectl rollout status deployment/gaussian-plume -n gaussian-plume
# Rollout'un tamamlanmasını bekle ve durumu göster

kubectl describe pod -n gaussian-plume <pod-name>
# Pod hakkında detaylı bilgi: events, volume mount'lar, env değişkenleri
# Pod başlamıyorsa hata ayıklamak için kullanılır

# ── Log Kontrolleri ─────────────────────────────────────────────────────────

# Gaussian-plume uygulama logları (canlı izleme)
kubectl logs -n gaussian-plume -l app=gaussian-plume -f
# -f: follow → yeni satırlar geldikçe ekrana yazar; Ctrl+C ile çık

# Gaussian-plume — son 100 satır
kubectl logs -n gaussian-plume -l app=gaussian-plume --tail=100

# Gitea logları
kubectl logs -n gitea -l app.kubernetes.io/name=gitea --tail=50
# Gitea'nın git işlemleri, kullanıcı hataları burada görünür

# Gitea configure-gitea init container logu (başlamıyorsa buraya bak)
kubectl logs -n gitea <gitea-pod-adı> -c configure-gitea
# Pod adını öğrenmek için: kubectl get pods -n gitea

# Gitea runner logları
kubectl logs -n gitea-runner -l app=gitea-runner --tail=50 -f
# Pipeline adımlarının gerçek çıktısı burada görünür

# Prometheus logları
kubectl logs -n monitoring -l app.kubernetes.io/name=prometheus --tail=50
# Scrape hataları, alerting sorunları burada görünür

# Grafana logları
kubectl logs -n monitoring -l app.kubernetes.io/name=grafana --tail=50
# Datasource bağlantı hataları, provisioning sorunları burada görünür

# Loki logları
kubectl logs -n monitoring -l app=loki --tail=50
# Log ingestion hataları burada görünür

# Promtail logları (log toplama ajanı)
kubectl logs -n monitoring -l app=loki-promtail --tail=50
# Hangi dosyaları okuduğunu, Loki'ye gönderip göndermediğini gösterir

# Önceki (crash olan) container'ın loglarını gör
kubectl logs -n <namespace> <pod-adı> --previous
# CrashLoopBackOff durumunda crash öncesi logu okumak için kullanılır

# Tüm namespace'lerde hata veren pod'ları filtrele
kubectl get pods -A | grep -v Running | grep -v Completed
# Running veya Completed olmayanları listeler → sorunlu pod'ları hızlıca bulur
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

---

## Uygulamayı Kapatma

Bilgisayarı kapatmadan önce veya servisler gerekmediğinde aşağıdaki sırayla kapat:

```bash
# 1) Gaussian-plume uygulamasını sıfır replikaya indir (pod silinir, deployment kalır)
kubectl scale deployment/gaussian-plume --replicas=0 -n gaussian-plume
# replicas=0 → pod yok, deployment yaml'ı kaybolmaz; tekrar açmak kolay

# 2) Gitea Actions runner'ı durdur
kubectl scale deployment/gitea-runner --replicas=0 -n gitea-runner
# CI/CD tetiklenemez; runner kaydı Gitea'da kalır

# 3) Gitea ve Valkey'i durdur
kubectl scale deployment/gitea -n gitea --replicas=0
# Gitea UI ve Git servisi kapanır; veritabanı PVC'de saklanır
kubectl scale statefulset/gitea-valkey-cluster -n gitea --replicas=0
# Valkey (cache/session) durdurulur; Gitea açılırken 3 replica ile başlatılmalı

# 4) Monitoring stack'i durdur — Prometheus & Grafana
kubectl scale deployment/monitoring-grafana --replicas=0 -n monitoring
kubectl scale deployment/monitoring-kube-state-metrics --replicas=0 -n monitoring
kubectl scale deployment/monitoring-kube-prometheus-operator --replicas=0 -n monitoring
# StatefulSet olan Prometheus'u da durdur
kubectl scale statefulset/prometheus-monitoring-kube-prometheus-prometheus --replicas=0 -n monitoring

# 5) Loki'yi durdur
kubectl scale statefulset/loki -n monitoring --replicas=0
# Log toplama durur; veriler PVC'de kalır

# 6) Promtail ve node-exporter DaemonSet'lerini dondur (replicas komutu çalışmaz → nodeSelector hilesi)
kubectl patch daemonset/loki-promtail -n monitoring \
  --type=json \
  -p='[{"op":"add","path":"/spec/template/spec/nodeSelector","value":{"non-existing":"true"}}]'
kubectl patch daemonset/monitoring-prometheus-node-exporter -n monitoring \
  --type=json \
  -p='[{"op":"add","path":"/spec/template/spec/nodeSelector","value":{"non-existing":"true"}}]'
# nodeSelector eşleşmediği için pod schedule edilmez → tüm pod'lar silinir

# 7) Local registry'yi durdur (Docker container)
docker stop local-registry
# Registry container durur; image katmanları Docker volume'da kalır

# 8) Tüm pod'ların kapandığını doğrula
kubectl get pods -A
# Beklenen çıktı: kube-system dışında Running pod olmamalı
```

> **Not:** Docker Desktop'ı kapatmana gerek yok. Kubernetes açık kalabilir, sadece uygulama pod'ları kapalıdır.

---

## Uygulamayı Açma

Servisleri tekrar başlatmak için aşağıdaki sırayı izle:

```bash
# 1) Local Docker registry'yi başlat
docker start local-registry
# local-registry container tekrar ayağa kalkar → :5000 erişilebilir olur

# 2) Gitea Valkey cluster'ını başlat (önce Valkey, sonra Gitea — zorunlu sıra)
kubectl scale statefulset/gitea-valkey-cluster -n gitea --replicas=3
# Valkey cluster modu için minimum 3 node gerekir; 1 replica ile cluster kurulamaz
# 3 pod da Running 1/1 olana kadar bekle:
kubectl get pods -n gitea -w
# Tüm valkey pod'ları hazır olunca Ctrl+C ile çık

# 3) Gitea'yı başlat
kubectl scale deployment/gitea -n gitea --replicas=1
# Gitea pod başlayana kadar bekle
kubectl rollout status deployment/gitea -n gitea
# "successfully rolled out" mesajı gelince devam et

# 4) Gitea runner'ı başlat
kubectl scale deployment/gitea-runner --replicas=1 -n gitea-runner
# Runner Gitea'ya bağlanır ve "Idle" durumuna geçer

# 5) Gaussian-plume uygulamasını başlat
kubectl scale deployment/gaussian-plume --replicas=1 -n gaussian-plume
kubectl rollout status deployment/gaussian-plume -n gaussian-plume
# Uygulama http://localhost:30501 adresinde erişilebilir olur

# 6) Prometheus & Grafana'yı başlat
kubectl scale deployment/monitoring-grafana --replicas=1 -n monitoring
kubectl scale deployment/monitoring-kube-state-metrics --replicas=1 -n monitoring
kubectl scale deployment/monitoring-kube-prometheus-operator --replicas=1 -n monitoring
kubectl scale statefulset/prometheus-monitoring-kube-prometheus-prometheus --replicas=1 -n monitoring
# Grafana: http://localhost:30300  (admin / prom-operator)
# Prometheus: http://localhost:30090

# 7) Loki'yi başlat
kubectl scale statefulset/loki -n monitoring --replicas=1

# 8) Promtail ve node-exporter DaemonSet'lerini geri aç (nodeSelector'ı kaldır)
kubectl patch daemonset/loki-promtail -n monitoring \
  --type=json \
  -p='[{"op":"remove","path":"/spec/template/spec/nodeSelector"}]'
kubectl patch daemonset/monitoring-prometheus-node-exporter -n monitoring \
  --type=json \
  -p='[{"op":"remove","path":"/spec/template/spec/nodeSelector"}]'
# nodeSelector kaldırılır → pod'lar tekrar schedule edilir ve log toplamaya başlar

# 9) Tüm servislerin ayakta olduğunu doğrula
kubectl get pods -A
# Tüm pod'lar Running/Ready olmalı

# Hızlı erişim adresleri:
# Uygulama  → http://localhost:30501
# Gitea     → http://localhost:30880
# Grafana   → http://localhost:30300
# Prometheus→ http://localhost:30090
```
