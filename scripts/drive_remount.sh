# wsl could silently fail to write to the drive, this script is to fix that
sudo mount -o remount,metadata,uid=$(id -u),gid=$(id -g),umask=0002 /mnt/c