# Minimal budget-first network: one VPC, one public subnet, IGW egress route.
# No NAT gateway, no ALB, no additional subnets. VPC CIDR deliberately does not
# overlap k3s defaults (cluster 10.42.0.0/16, services 10.43.0.0/12).
resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = local.vpc_name }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = { Name = "${local.vpc_name}-igw" }
}

# Single public subnet. The instance auto-assigns an EPHEMERAL public IPv4
# (changes on every stop/start; billed per hour while associated).
resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.0.1.0/24"
  map_public_ip_on_launch = true

  tags = { Name = local.subnet_name }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = { Name = "${local.vpc_name}-public-rt" }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

# NO ingress rules at all. Every operator path goes through Systems Manager
# (Session Manager, Run Command, port forwarding to NodePort 30080) over the
# agent's outbound HTTPS channel. Egress stays open so the SSM agent, cloud-init,
# CloudWatch agent and AWS CLI can reach AWS endpoints through the IGW.
resource "aws_security_group" "node" {
  name        = local.sg_name
  description = "${var.project_prefix} k3s node: no inbound; egress-only for SSM/ECR/S3 through IGW"
  vpc_id      = aws_vpc.main.id

  egress {
    description = "All outbound (SSM agent, ECR/S3 pulls, k3s install, CloudWatch agent)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = local.sg_name }
}
