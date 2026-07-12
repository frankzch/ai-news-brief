#!/usr/bin/env python
"""
管理员RSS源管理脚本

用法:
    添加单个源: python admin_rss.py add <url> <name> [description]
    列出所有源: python admin_rss.py list
    删除源:     python admin_rss.py delete <id>
"""

import sys
import argparse
from src.user_db import UserDatabase


def add_source(url: str, name: str, description: str = None, category: str = None, importance_score: int = 0):
    """添加RSS源"""
    db = UserDatabase()
    source_id = db.add_rss_source(url, name, description, category=category, importance_score=importance_score)
    if source_id:
        print(f"✓ 成功添加RSS源: {name} (ID: {source_id})")
        print(f"  URL: {url}")
        if category:
            print(f"  类别: {category}")
        print(f"  重要性分数: {importance_score}")
    else:
        print(f"✗ 添加失败: URL已存在或发生错误")


def list_sources():
    """列出所有RSS源"""
    db = UserDatabase()
    sources = db.get_all_rss_sources()
    
    if not sources:
        print("暂无RSS源")
        return
    
    print(f"\n{'='*60}")
    print(f"共 {len(sources)} 个RSS源")
    print(f"{'='*60}\n")
    
    for src in sources:
        print(f"ID: {src['id']}")
        print(f"  名称: {src['name']}")
        print(f"  URL:  {src['url']}")
        if src.get('description'):
            print(f"  描述: {src['description']}")
        print(f"  重要性分数: {src.get('importance_score', 0)}")
        print(f"  添加时间: {src['created_at']}")
        print()


def delete_source(source_id: int):
    """删除RSS源"""
    db = UserDatabase()
    source = db.get_rss_source_by_id(source_id)
    
    if not source:
        print(f"✗ 未找到 ID={source_id} 的RSS源")
        return
    
    # 确认删除
    print(f"即将删除: {source['name']} ({source['url']})")
    confirm = input("确认删除? (y/N): ").strip().lower()
    
    if confirm == 'y':
        db.delete_rss_sources([source_id])
        print(f"✓ 已删除RSS源: {source['name']}")
    else:
        print("已取消")


def main():
    parser = argparse.ArgumentParser(
        description="管理员RSS源管理工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    
    subparsers = parser.add_subparsers(dest='command', help='可用命令')
    
    # add 命令
    add_parser = subparsers.add_parser('add', help='添加RSS源')
    add_parser.add_argument('url', help='RSS源URL')
    add_parser.add_argument('name', help='RSS源名称')
    add_parser.add_argument('description', nargs='?', default=None, help='RSS源描述（可选）')
    add_parser.add_argument('--category', '-c', type=str, default=None, help='RSS源类别（可选）')
    add_parser.add_argument('--importance-score', '-i', type=int, default=0, help='重要性分数（可选，默认0）')
    
    # list 命令
    subparsers.add_parser('list', help='列出所有RSS源')
    
    # delete 命令
    del_parser = subparsers.add_parser('delete', help='删除RSS源')
    del_parser.add_argument('id', type=int, help='要删除的RSS源ID')
    
    args = parser.parse_args()
    
    if args.command == 'add':
        add_source(args.url, args.name, args.description, args.category, args.importance_score)
    elif args.command == 'list':
        list_sources()
    elif args.command == 'delete':
        delete_source(args.id)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
