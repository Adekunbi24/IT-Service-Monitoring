output "public_ip" { value = aws_instance.server.public_ip }
output "instance_id" { value = aws_instance.server.id }
output "note" { value = "DNS can point to the public IP, but IP may change on instance replacement. Configure app manually after provisioning; see README." }
