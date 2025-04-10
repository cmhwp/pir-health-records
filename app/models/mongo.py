from flask import current_app
from pymongo import MongoClient
import json
from datetime import datetime

class MongoConnector:
    def __init__(self):
        self.client = None
        self.db = None
    
    def init_app(self, app):
        """Initialize MongoDB connection with Flask app configuration"""
        mongodb_uri = app.config.get('MONGO_URI')
        db_name = app.config.get('MONGO_DB_NAME')
        
        self.client = MongoClient(mongodb_uri)
        self.db = self.client[db_name]
        
        # Create necessary collections and indexes if they don't exist
        if 'health_records' not in self.db.list_collection_names():
            self.db.create_collection('health_records')
            self.db.health_records.create_index('record_id', unique=True)
        
        if 'pir_indexes' not in self.db.list_collection_names():
            self.db.create_collection('pir_indexes')
            self.db.pir_indexes.create_index('keyword')
    
    def store_health_record(self, record_id, content):
        """Store encrypted health record content in MongoDB"""
        record_doc = {
            'record_id': record_id,
            'content': content,
            'created_at': datetime.now(),
            'updated_at': datetime.now()
        }
        
        result = self.db.health_records.insert_one(record_doc)
        return str(result.inserted_id)
    
    def get_health_record(self, record_id):
        """Retrieve a health record by its ID"""
        record = self.db.health_records.find_one({'record_id': record_id})
        return record
    
    def update_health_record(self, record_id, content):
        """Update an existing health record"""
        result = self.db.health_records.update_one(
            {'record_id': record_id},
            {
                '$set': {
                    'content': content,
                    'updated_at': datetime.now()
                }
            }
        )
        return result.modified_count > 0
    
    def add_to_pir_index(self, keyword, record_id, encrypted_location=None):
        """Add a record to the PIR index by keyword"""
        # Upsert - add record_id to the list if keyword exists, create new doc otherwise
        result = self.db.pir_indexes.update_one(
            {'keyword': keyword},
            {
                '$addToSet': {'record_ids': record_id},
                '$set': {'updated_at': datetime.now()},
                '$setOnInsert': {
                    'created_at': datetime.now(),
                    'encrypted_location': encrypted_location
                }
            },
            upsert=True
        )
        return result.modified_count > 0 or result.upserted_id is not None
    
    def search_pir_index(self, keyword):
        """Search the PIR index for a keyword (this is NOT the private query, just for testing)"""
        index_doc = self.db.pir_indexes.find_one({'keyword': keyword})
        if index_doc:
            return index_doc.get('record_ids', [])
        return []

# Create a singleton instance
mongo_client = MongoConnector() 