import os
import time
import json
import traceback
from datetime import datetime, timedelta
from dotenv import load_dotenv
import tweepy

# Load environment variables
load_dotenv()

# X (Twitter) API credentials
TWITTER_API_KEY = os.getenv("TWITTER_API_KEY")
TWITTER_API_SECRET = os.getenv("TWITTER_API_SECRET")
TWITTER_ACCESS_TOKEN = os.getenv("TWITTER_ACCESS_TOKEN")
TWITTER_ACCESS_SECRET = os.getenv("TWITTER_ACCESS_SECRET")
TWITTER_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN")

# Your X (Twitter) username (without the @ symbol)
TWITTER_USERNAME = os.getenv("TWITTER_USERNAME")

# Telegram chat details (not used for Telegram API directly)
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Initialize X (Twitter) API v2 client
client = tweepy.Client(
    bearer_token=TWITTER_BEARER_TOKEN,
    consumer_key=TWITTER_API_KEY,
    consumer_secret=TWITTER_API_SECRET,
    access_token=TWITTER_ACCESS_TOKEN,
    access_token_secret=TWITTER_ACCESS_SECRET,
    wait_on_rate_limit=True  # This is crucial - lets tweepy handle rate limits
)

# State management
bot_state = {
    "user_id": None,
    "last_follower_count": 0,
    "last_tweet_id": None,
    "processed_mentions": set(),
    "tweet_metrics": {},
    "notifications": [],
    "last_check_time": {}
}

# File paths
STATE_FILE = "bot_state.json"
NOTIFICATIONS_FILE = "notifications.txt"

def save_state():
    """Save the bot state to disk"""
    # Convert processed_mentions set to list for JSON serialization
    save_data = bot_state.copy()
    save_data["processed_mentions"] = list(bot_state["processed_mentions"])
    
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(save_data, f, default=str)
        print(f"State saved to {STATE_FILE}")
    except Exception as e:
        print(f"Error saving state: {e}")

def load_state():
    """Load the bot state from disk if it exists"""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                data = json.load(f)
            
            # Convert processed_mentions back to a set
            data["processed_mentions"] = set(data["processed_mentions"])
            
            # Update bot_state
            bot_state.update(data)
            print(f"State loaded from {STATE_FILE}")
            return True
        except Exception as e:
            print(f"Error loading state: {e}")
    return False

def log_notification(message):
    """Log a notification to a file instead of sending via Telegram"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] {message}\n"
    
    # Add to in-memory list
    bot_state["notifications"].append(log_entry)
    
    # Write to file
    try:
        with open(NOTIFICATIONS_FILE, 'a') as f:
            f.write(log_entry)
        print(f"Notification logged: {message[:50]}...")
    except Exception as e:
        print(f"Error logging notification: {e}")

def should_check(check_type, min_interval_minutes=15):
    """Determine if enough time has passed since last check"""
    now = datetime.now()
    last_check = bot_state["last_check_time"].get(check_type)
    
    if last_check is None:
        bot_state["last_check_time"][check_type] = now
        return True
    
    # Convert string date back to datetime if needed
    if isinstance(last_check, str):
        try:
            last_check = datetime.fromisoformat(last_check)
        except:
            last_check = now - timedelta(minutes=min_interval_minutes+1)
    
    time_passed = now - last_check
    should_proceed = time_passed.total_seconds() / 60 >= min_interval_minutes
    
    if should_proceed:
        bot_state["last_check_time"][check_type] = now
        
    return should_proceed

def safe_api_call(func, *args, **kwargs):
    """Make an API call with rate limit and error handling"""
    max_retries = 3
    retry_count = 0
    
    while retry_count < max_retries:
        try:
            return func(*args, **kwargs)
        except tweepy.errors.TooManyRequests:
            retry_count += 1
            wait_time = 60 * (2 ** retry_count)  # Exponential backoff: 2min, 4min, 8min
            print(f"Rate limited. Waiting {wait_time} seconds before retry {retry_count}/{max_retries}...")
            time.sleep(wait_time)
        except Exception as e:
            print(f"API call error: {e}")
            traceback.print_exc()
            retry_count += 1
            wait_time = 30 * retry_count
            print(f"Waiting {wait_time} seconds before retry {retry_count}/{max_retries}...")
            time.sleep(wait_time)
    
    print(f"Failed after {max_retries} attempts")
    return None

def check_new_followers():
    """Check for new followers and log notifications"""
    if not should_check("followers", 60):  # Check at most once per hour
        return
    
    print("Checking for new followers...")
    
    # Get current follower count
    user_data = safe_api_call(
        client.get_user,
        id=bot_state["user_id"], 
        user_fields=['public_metrics']
    )
    
    if not user_data or not user_data.data:
        print("Failed to get user data")
        return
    
    current_follower_count = user_data.data.public_metrics['followers_count']
    
    # If this is the first run, just store the current count
    if bot_state["last_follower_count"] == 0:
        bot_state["last_follower_count"] = current_follower_count
        save_state()
        return
    
    # Check if we have new followers
    follower_diff = current_follower_count - bot_state["last_follower_count"]
    
    if follower_diff != 0:
        message = f"Follower count changed by {follower_diff}. New total: {current_follower_count}"
        log_notification(message)
    
    # Update the last follower count
    bot_state["last_follower_count"] = current_follower_count
    save_state()

def check_new_tweets():
    """Check for your new tweets and track engagement"""
    if not should_check("tweets", 30):  # Check every 30 minutes
        return
    
    print("Checking for new tweets...")
    
    # Get your recent tweets
    tweets = safe_api_call(
        client.get_users_tweets,
        id=bot_state["user_id"],
        max_results=5,
        tweet_fields=['created_at', 'public_metrics']
    )
    
    if not tweets or not tweets.data:
        print("No tweets found or error retrieving tweets")
        return
    
    newest_tweet_id = tweets.data[0].id
    
    # If this is the first run, just store the latest tweet ID
    if bot_state["last_tweet_id"] is None:
        bot_state["last_tweet_id"] = newest_tweet_id
        save_state()
        return
    
    # Check if there's a new tweet
    if str(newest_tweet_id) != str(bot_state["last_tweet_id"]):
        newest_tweet = tweets.data[0]
        tweet_url = f"https://twitter.com/{TWITTER_USERNAME}/status/{newest_tweet.id}"
        
        metrics = newest_tweet.public_metrics
        message = (
            f"New tweet posted!\n"
            f"Content: {newest_tweet.text[:100]}...\n"
            f"Replies: {metrics['reply_count']}\n"
            f"Retweets: {metrics['retweet_count']}\n"
            f"Likes: {metrics['like_count']}\n"
            f"Quotes: {metrics['quote_count']}\n"
            f"URL: {tweet_url}"
        )
        log_notification(message)
        
        # Update the last tweet ID
        bot_state["last_tweet_id"] = newest_tweet_id
        save_state()

def check_mentions():
    """Check for new mentions and interactions"""
    if not should_check("mentions", 45):  # Check every 45 minutes
        return
    
    print("Checking for mentions...")
    
    # Get recent mentions
    mentions = safe_api_call(
        client.get_users_mentions,
        id=bot_state["user_id"],
        max_results=10,
        tweet_fields=['created_at', 'public_metrics', 'author_id']
    )
    
    if not mentions or not mentions.data:
        print("No mentions found or error retrieving mentions")
        return
    
    # Get user data for all authors in one request
    author_ids = [tweet.author_id for tweet in mentions.data]
    authors = safe_api_call(client.get_users, ids=author_ids)
    author_map = {user.id: user for user in authors.data} if authors and authors.data else {}
    
    new_mentions_found = False
    
    for tweet in mentions.data:
        # Skip already processed mentions
        if str(tweet.id) in bot_state["processed_mentions"]:
            continue
        
        # Add to processed set
        bot_state["processed_mentions"].add(str(tweet.id))
        new_mentions_found = True
        
        # Only notify about new mentions (last day)
        tweet_time = tweet.created_at
        now = datetime.now(tweet_time.tzinfo)
        if (now - tweet_time).total_seconds() > 86400:  # 24 hours in seconds
            continue
        
        # Get author username
        author = author_map.get(tweet.author_id)
        author_username = author.username if author else "unknown"
        
        tweet_url = f"https://twitter.com/{author_username}/status/{tweet.id}"
        metrics = tweet.public_metrics
        
        message = (
            f"New mention from @{author_username}!\n"
            f"Content: {tweet.text[:100]}...\n"
            f"Replies: {metrics['reply_count']}\n"
            f"Retweets: {metrics['retweet_count']}\n"
            f"Likes: {metrics['like_count']}\n"
            f"URL: {tweet_url}"
        )
        log_notification(message)
    
    # Keep the set size manageable and save state if new mentions were found
    if new_mentions_found:
        if len(bot_state["processed_mentions"]) > 100:
            # Convert to list, keep most recent 50, convert back to set
            bot_state["processed_mentions"] = set(list(bot_state["processed_mentions"])[-50:])
        save_state()

def check_tweet_engagement():
    """Check engagement changes on recent tweets"""
    if not should_check("engagement", 120):  # Check every 2 hours
        return
    
    if bot_state["last_tweet_id"] is None:
        return
    
    print("Checking tweet engagement...")
    
    # Get your recent tweets
    tweets = safe_api_call(
        client.get_users_tweets,
        id=bot_state["user_id"],
        max_results=5,
        tweet_fields=['created_at', 'public_metrics']
    )
    
    if not tweets or not tweets.data:
        print("No tweets found or error retrieving tweets")
        return
    
    engagement_updates = False
    
    for tweet in tweets.data:
        tweet_id = str(tweet.id)
        metrics = tweet.public_metrics
        
        # Skip if we don't have previous metrics for this tweet
        if tweet_id not in bot_state["tweet_metrics"]:
            bot_state["tweet_metrics"][tweet_id] = metrics
            engagement_updates = True
            continue
        
        prev = bot_state["tweet_metrics"][tweet_id]
        
        # Check for significant engagement changes
        significant_change = False
        change_text = []
        
        # Check likes (10+ new likes)
        like_diff = metrics['like_count'] - prev['like_count']
        if like_diff >= 10:
            significant_change = True
            change_text.append(f"+{like_diff} likes")
        
        # Check retweets (5+ new retweets)
        rt_diff = metrics['retweet_count'] - prev['retweet_count']
        if rt_diff >= 5:
            significant_change = True
            change_text.append(f"+{rt_diff} retweets")
        
        # Check replies (3+ new replies)
        reply_diff = metrics['reply_count'] - prev['reply_count']
        if reply_diff >= 3:
            significant_change = True
            change_text.append(f"+{reply_diff} replies")
        
        if significant_change:
            tweet_url = f"https://twitter.com/{TWITTER_USERNAME}/status/{tweet_id}"
            message = (
                f"Engagement update on tweet!\n"
                f"Content: {tweet.text[:100]}...\n"
                f"{', '.join(change_text)}\n"
                f"Current totals: {metrics['reply_count']} replies, {metrics['retweet_count']} retweets, {metrics['like_count']} likes\n"
                f"URL: {tweet_url}"
            )
            log_notification(message)
        
        # Update stored metrics
        bot_state["tweet_metrics"][tweet_id] = metrics
        engagement_updates = True
    
    # Save state if there were updates
    if engagement_updates:
        # Keep only the 10 most recent tweets in our metrics tracking
        if len(bot_state["tweet_metrics"]) > 10:
            # Sort by tweet ID (newer IDs are larger)
            sorted_ids = sorted(bot_state["tweet_metrics"].keys(), reverse=True)
            # Keep only the 10 most recent
            bot_state["tweet_metrics"] = {
                tweet_id: bot_state["tweet_metrics"][tweet_id] 
                for tweet_id in sorted_ids[:10]
            }
        
        save_state()

def main():
    """Main function to run the bot"""
    print("Starting X Notification Bot...")
    print("This version logs notifications to file instead of using Telegram API")
    print(f"Notifications will be saved to: {NOTIFICATIONS_FILE}")
    
    # Create notification file if it doesn't exist
    if not os.path.exists(NOTIFICATIONS_FILE):
        with open(NOTIFICATIONS_FILE, 'w') as f:
            f.write(f"X Notification Bot Started at {datetime.now()}\n")
    
    # Try to load previous state
    if not load_state():
        print("No previous state found. Starting fresh.")
    
    # Initialize with a long delay
    print("Initializing... waiting 5 seconds")
    time.sleep(5)
    
    try:
        # Get user ID if not already in state
        if not bot_state["user_id"]:
            print(f"Looking up user ID for @{TWITTER_USERNAME}...")
            user = safe_api_call(client.get_user, username=TWITTER_USERNAME)
            if user and user.data:
                bot_state["user_id"] = user.data.id
                print(f"User ID: {bot_state['user_id']}")
                save_state()
            else:
                print("Failed to get user ID. Check your Twitter credentials and username.")
                return
        
        # Get initial follower count if not already in state
        if bot_state["last_follower_count"] == 0:
            print("Getting initial follower count...")
            user_data = safe_api_call(
                client.get_user, 
                id=bot_state["user_id"], 
                user_fields=['public_metrics']
            )
            if user_data and user_data.data:
                bot_state["last_follower_count"] = user_data.data.public_metrics['followers_count']
                print(f"Initial follower count: {bot_state['last_follower_count']}")
                save_state()
        
        # Get initial latest tweet if not already in state
        if bot_state["last_tweet_id"] is None:
            print("Getting initial tweet ID...")
            tweets = safe_api_call(
                client.get_users_tweets,
                id=bot_state["user_id"],
                max_results=5
            )
            if tweets and tweets.data:
                bot_state["last_tweet_id"] = tweets.data[0].id
                print(f"Initial latest tweet ID: {bot_state['last_tweet_id']}")
                save_state()
        
        # Log initial message
        log_notification("X Notification Bot is now running!")
        
        # Main loop
        consecutive_errors = 0
        cycle_count = 0
        
        while True:
            try:
                cycle_count += 1
                print(f"\n--- Update Cycle #{cycle_count} ---")
                
                # Stagger checks to avoid burst API usage
                check_new_followers()
                time.sleep(10)
                
                check_new_tweets()
                time.sleep(10)
                
                check_mentions()
                time.sleep(10)
                
                check_tweet_engagement()
                
                consecutive_errors = 0  # Reset error counter on success
                
                # Wait before next check cycle - longer interval for fewer API calls
                wait_time = 600  # 10 minutes
                print(f"Update cycle complete. Sleeping for {wait_time/60} minutes...")
                time.sleep(wait_time)
                
            except KeyboardInterrupt:
                print("\nBot stopped by user.")
                log_notification("Bot stopped by user.")
                save_state()
                break
            except Exception as e:
                consecutive_errors += 1
                backoff_time = min(300 * consecutive_errors, 3600)  # Max 1 hour backoff
                
                error_message = f"Error in monitoring loop: {str(e)}"
                print(error_message)
                traceback.print_exc()
                print(f"Consecutive errors: {consecutive_errors}. Backing off for {backoff_time/60} minutes")
                
                # Log errors
                if consecutive_errors <= 3:
                    log_notification(f"Error: {str(e)}")
                
                # Save state on error
                save_state()
                
                time.sleep(backoff_time)
    
    except KeyboardInterrupt:
        print("\nBot stopped by user.")
        log_notification("Bot stopped by user.")
        save_state()
    except Exception as e:
        error_message = f"Fatal error: {str(e)}"
        print(error_message)
        traceback.print_exc()
        log_notification(error_message)
        save_state()

if __name__ == "__main__":
    main()