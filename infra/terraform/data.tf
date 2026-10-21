data "aws_caller_identity" "current" {}

# AL2023 x86_64 AMI id via the public SSM parameter — never a hardcoded AMI id.
data "aws_ssm_parameter" "al2023_x86" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

# Resolve + verify the parameter value: fails loudly at plan time if it is not
# a public Amazon-owned x86_64 AMI.
data "aws_ami" "al2023_x86" {
  owners = ["amazon"]

  filter {
    name   = "image-id"
    values = [data.aws_ssm_parameter.al2023_x86.value]
  }

  filter {
    name   = "architecture"
    values = ["x86_64"]
  }
}
