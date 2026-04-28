export TMPDIR=~/tmp
mkdir -p ~/tmp
rm -rf .terraform .terraform.lock.hcl
terraform init
