# infra/terraform

CI/CD 파이프라인(GitHub push -> Cloud Build -> Cloud Run) 프로비저닝용 Terraform 스켈레톤.

```
cp terraform.tfvars.example terraform.tfvars   # 값 채우기
terraform init
terraform plan -out=tfplan
terraform apply tfplan
```

전제조건: Cloud Build 콘솔에서 GitHub 호스트 연결(`host_connection_name`)을 먼저 수동으로 만들어야 함.
