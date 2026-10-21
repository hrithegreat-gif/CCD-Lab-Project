# Single x86 k3s node. Explicitly NOT: ASG, launch template fleet, EKS, GPU.
resource "aws_instance" "k3s_node" {
  ami                    = data.aws_ami.al2023_x86.id
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.node.id]
  iam_instance_profile   = aws_iam_instance_profile.node.name

  # The auto-stop timer runs `systemctl poweroff`; this makes the guest-initiated
  # shutdown STOP the instance so the encrypted EBS volume and node state are
  # retained. No AWS self-stop API call from the host is needed.
  instance_initiated_shutdown_behavior = "stop"

  # IMDSv2 required. Hop limit 1 is DELIBERATE: host-level processes (SSM agent,
  # cloud-init, AWS CLI, CloudWatch agent, journal shipper) are one hop away and
  # work, but k3s pods are an extra network hop away and therefore CANNOT reach
  # IMDS — workloads cannot inherit the node role credentials. Image pulls and
  # deployment commands run host-side by design. Only raise this after an
  # explicit security review.
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  root_block_device {
    volume_size           = var.root_volume_size
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = true
  }

  # Basic (free) monitoring; detailed 1-minute metrics cost extra.
  monitoring = false

  # Bootstrap: pinned k3s + CloudWatch agent + auto-stop timer.
  # No git, no bundle pull, no image pull, no deployment at boot.
  # RAW templatefile — never pre-base64-encode: the provider encodes user_data
  # itself and pre-encoding double-encodes the payload.
  user_data = templatefile("${path.module}/user_data.tftpl", {
    aws_region            = var.aws_region
    project_prefix        = var.project_prefix
    k3s_version           = var.k3s_version
    autostop_hours        = var.autostop_hours
    log_group_k3s_pods    = local.log_group_k3s_pods
    log_group_k3s_service = local.log_group_k3s_service
    log_group_bootstrap   = local.log_group_bootstrap
  })

  # Opt INTO replacement on user_data change (provider >= 4.28 default is
  # update-in-place). WIPE DISCLOSURE: a replacement discards the node's EBS
  # state (old root volume is deleted via delete_on_termination) and assigns a
  # NEW ephemeral public IPv4.
  user_data_replace_on_change = true

  # Give the instance profile its SSM policy and scoped permissions a beat to
  # propagate before first boot.
  depends_on = [
    aws_iam_role_policy_attachment.node_ssm_core,
    aws_iam_role_policy.node_scoped
  ]

  tags = { Name = "${var.project_prefix}-k3s-node" }
}

# NOTE: any user_data input change (k3s_version, autostop_hours, log group
# names) replaces the instance: EBS node state is wiped and the ephemeral
# public IPv4 changes. Teardown is a documented, guarded operator action
# (see docs/AWS_RUNBOOK.md) — prevent_destroy is intentionally not set.
